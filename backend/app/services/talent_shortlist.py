"""AI talent shortlist: role description → ranked, explained candidates.

The employer pastes a job description (or picks one of their postings):

1. **Structure** — P2 turns the JD into title / seniority / years / required
   and preferred skills (structured output).  Every LLM skill must appear in
   the JD text (``validate_skill_tokens``); a deterministic surface-scan
   (``ingest.extract_skills``) runs too, and the union is used, so recall
   doesn't depend on the model and nothing invented gets in.  No provider →
   deterministic structuring only.
2. **Retrieve** — candidate pool = seekers who opted in
   (``profiles.open_to_recruiters``) plus, for a posting, its applicants.
   Two legs, fused by RRF: pgvector KNN (JD embedding ↔ profile embedding)
   and required-skill overlap.
3. **Score** — the *same* 8-component engine seekers see
   (``_ProfileContext.inputs_for`` → ``score_pair``) on a transient Job built
   from the structured JD.  The LLM never assigns a score.
4. **Explain** — per candidate, a "why they fit / what's missing" pair built
   from engine facts.  The top few may be rewritten by the LLM; the rewrite
   is discarded unless every digit is preserved and no unknown skill appears.

Privacy: non-applicants are shown with first name + last initial and no
contact details; employers see contact info only for people who applied.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, Field
from sqlalchemy import bindparam, select, text
from sqlalchemy.orm import Session, selectinload

from app.models.application import Application
from app.models.enums import EmploymentType, ExperienceLevel, JobStatus, UserRole
from app.models.job import Job
from app.models.profile import Profile
from app.models.skill import JobSkill, ProfileSkill, Skill
from app.models.user import User

log = logging.getLogger(__name__)

POOL_PER_LEG = 100
RRF_K = 60
LLM_EXPLAIN_TOP_N = 5


# ---------------------------------------------------------------------------
# 1. JD structuring
# ---------------------------------------------------------------------------

class _SkillReq(BaseModel):
    name: str
    importance: float = Field(default=1.0, ge=0.1, le=2.0)


class StructuredJd(BaseModel):
    title: str = ""
    seniority: str | None = None
    experience_years_min: float | None = None
    required_skills: list[_SkillReq] = Field(default_factory=list)
    preferred_skills: list[_SkillReq] = Field(default_factory=list)


@dataclass
class JdSpec:
    title: str
    level: ExperienceLevel
    years_min: float | None
    required: list[str]
    preferred: list[str]
    location: str | None
    is_remote: bool
    family: str | None
    description: str
    structured_by: str            # "llm+rules" | "rules"
    dropped_llm_skills: list[str] = field(default_factory=list)


def _canonical(names: list[str]) -> list[str]:
    seen: set[str] = set()
    out = []
    for n in names:
        key = n.strip().lower()
        if key and key not in seen:
            seen.add(key)
            out.append(n.strip())
    return out


def _title_from_line(line: str, limit: int = 80) -> str:
    """First JD line → a short title: cut at the first sentence/clause break."""
    head = re.split(r"[.!?;:|]\s|\s[-–—]\s|\(", line, maxsplit=1)[0].strip()
    if len(head) <= limit:
        return head
    return head[:limit].rsplit(" ", 1)[0].strip()


def structure_jd(jd_text: str, *, title_hint: str | None = None, location: str | None = None) -> JdSpec:
    from app.services import live_jobs
    from app.services.ingest import assign_family, detect_remote, extract_skills, map_seniority, parse_years

    text_ = (jd_text or "").strip()
    first_line = next((ln.strip() for ln in text_.splitlines() if ln.strip()), "")
    title = (title_hint or _title_from_line(first_line) or "Role").strip()

    rules = extract_skills(title, text_, live_jobs._surface_pairs())
    rule_required = [n for n, req in rules if req]
    rule_preferred = [n for n, req in rules if not req]
    years = parse_years(text_)

    llm_required: list[str] = []
    llm_preferred: list[str] = []
    llm_title = None
    llm_level = None
    dropped: list[str] = []
    structured_by = "rules"
    try:
        from app.services.llm import prompts, validators
        from app.services.llm.provider import get_provider

        provider = get_provider()
        if provider is not None:
            out, _usage = provider.complete_json(
                StructuredJd, prompts.p2_job_description(text_), max_tokens=900, route="P2_shortlist"
            )
            for bucket, target in ((out.required_skills, llm_required), (out.preferred_skills, llm_preferred)):
                names = [s.name for s in bucket if s.name.strip()]
                bad = set(validators.validate_skill_tokens(names, text_))
                for n in names:
                    if f"skill '{n.strip()}' does not appear in the source text" in bad:
                        dropped.append(n)
                    else:
                        target.append(n)
            llm_title = out.title.strip() or None
            llm_level = (out.seniority or "").strip().lower() or None
            if years is None and out.experience_years_min is not None:
                years = float(out.experience_years_min)
            structured_by = "llm+rules"
    except Exception:  # noqa: BLE001 - structuring degrades to rules
        log.info("P2 shortlist structuring failed; rules only", exc_info=True)

    title = title_hint or llm_title or title
    required = _canonical(llm_required + rule_required)
    preferred = [p for p in _canonical(llm_preferred + rule_preferred) if p.lower() not in {r.lower() for r in required}]
    try:
        level = ExperienceLevel(llm_level) if llm_level else map_seniority(title, years, text_)
    except ValueError:
        level = map_seniority(title, years, text_)

    return JdSpec(
        title=title[:200],
        level=level,
        years_min=years,
        required=required[:20],
        preferred=preferred[:15],
        location=location,
        is_remote=detect_remote(title, text_, location),
        family=assign_family(title, live_jobs._families()),
        description=text_[:8000],
        structured_by=structured_by,
        dropped_llm_skills=dropped,
    )


def _transient_job(db: Session, spec: JdSpec) -> Job:
    from sqlalchemy import func

    from app.models.job_family import JobFamily

    job = Job(
        id=uuid4(),
        source="shortlist",
        title=spec.title,
        description=spec.description,
        requirements=None,
        employment_type=EmploymentType.FULL_TIME,
        experience_level=spec.level,
        location=spec.location or "Not specified",
        is_remote=spec.is_remote,
        salary_min=None,
        salary_max=None,
        salary_currency="USD",
        status=JobStatus.PUBLISHED,
    )
    family = None
    if spec.family:
        row = db.scalar(select(JobFamily).where(func.lower(JobFamily.name) == spec.family.lower()))
        if row is not None:
            family = JobFamily(id=row.id, name=row.name, parent_id=row.parent_id)
    job.family = family
    job.company = None
    job.skills = [
        JobSkill(skill=Skill(name=n, category=None), is_required=True, importance_weight=1.4) for n in spec.required
    ] + [
        JobSkill(skill=Skill(name=n, category=None), is_required=False, importance_weight=1.0) for n in spec.preferred
    ]
    job.applications = []
    from app.services.embeddings import build_job_text, get_embedder

    job.embedding = get_embedder().encode_one(
        build_job_text(spec.title, None, spec.description, spec.required)
    )
    return job


# ---------------------------------------------------------------------------
# 2. Retrieval
# ---------------------------------------------------------------------------

def _candidate_ids(db: Session, job: Job, spec: JdSpec, applicant_profile_ids: set[UUID], exclude_user: UUID) -> list[UUID]:
    legs: list[list[UUID]] = []
    try:
        emb = "[" + ",".join(f"{float(v):.6f}" for v in job.embedding) + "]"
        rows = db.execute(
            text(
                "SELECT p.id FROM profiles p JOIN users u ON u.id = p.user_id "
                "WHERE p.open_to_recruiters AND u.is_active AND u.role = :role "
                "AND p.user_id <> :me AND p.embedding IS NOT NULL "
                "ORDER BY p.embedding <=> CAST(:emb AS vector) LIMIT :k"
            ),
            {"role": UserRole.JOB_SEEKER.name, "me": exclude_user, "emb": emb, "k": POOL_PER_LEG},
        ).all()
        legs.append([r[0] for r in rows])
    except Exception:  # noqa: BLE001
        db.rollback()
        log.exception("shortlist KNN leg failed")

    names = [n.lower() for n in spec.required + spec.preferred]
    if names:
        try:
            stmt = text(
                "SELECT p.id, COUNT(*) AS ov FROM profiles p "
                "JOIN users u ON u.id = p.user_id "
                "JOIN profile_skills ps ON ps.profile_id = p.id "
                "JOIN skills s ON s.id = ps.skill_id "
                "WHERE p.open_to_recruiters AND u.is_active AND u.role = :role AND p.user_id <> :me "
                "AND lower(s.name) IN :names GROUP BY p.id ORDER BY ov DESC, p.id LIMIT :k"
            ).bindparams(bindparam("names", expanding=True))
            rows = db.execute(
                stmt, {"role": UserRole.JOB_SEEKER.name, "me": exclude_user, "names": names, "k": POOL_PER_LEG}
            ).all()
            legs.append([r[0] for r in rows])
        except Exception:  # noqa: BLE001
            db.rollback()
            log.exception("shortlist skill leg failed")

    if applicant_profile_ids:
        legs.append(sorted(applicant_profile_ids, key=str))

    scores: dict[UUID, float] = {}
    for leg in legs:
        for rank, pid in enumerate(leg, start=1):
            scores[pid] = scores.get(pid, 0.0) + 1.0 / (RRF_K + rank)
    return sorted(scores, key=lambda p: (-scores[p], str(p)))[: POOL_PER_LEG]


# ---------------------------------------------------------------------------
# 3–4. Score + explain
# ---------------------------------------------------------------------------

class _Explain(BaseModel):
    why_fit: str = Field(max_length=400)
    gaps: str = Field(max_length=300)


def _template(spec: JdSpec, years: float | None, matched: list[str], missing: list[str], transferable: list[str]) -> tuple[str, str]:
    n_req = len(spec.required)
    have_req = [m for m in matched if m.lower() in {r.lower() for r in spec.required}]
    parts = []
    if n_req:
        parts.append(f"Covers {len(have_req)} of {n_req} required skills" + (f" ({', '.join(have_req[:4])})" if have_req else ""))
    elif matched:
        parts.append(f"Relevant skills: {', '.join(matched[:4])}")
    if years is not None:
        ask = f" vs {spec.years_min:g}+ asked" if spec.years_min is not None else ""
        parts.append(f"{years:g} yrs experience{ask}")
    if transferable:
        parts.append(f"adjacent experience in {', '.join(transferable[:2])}")
    why = "; ".join(parts) + "." if parts else "Background is semantically close to this role."
    if missing:
        gaps = "Missing: " + ", ".join(missing[:4]) + "."
    else:
        gaps = "No required-skill gaps."
    return why, gaps


def _llm_explain(spec: JdSpec, facts: dict[str, Any], why: str, gaps: str) -> tuple[str, str, bool]:
    from app.services.llm.provider import get_provider
    from app.services.llm.validators import validate_digits

    provider = get_provider()
    if provider is None:
        return why, gaps, False
    allowed_skills = {s.lower() for s in spec.required + spec.preferred + facts["matched"] + facts["transferable"]}
    prompt = (
        "You write a recruiter's two-line candidate note. Use ONLY these facts; never add skills, "
        "employers, numbers or claims that are not listed.\n"
        f"ROLE: {spec.title}\nFACTS: {facts}\nDRAFT why_fit: {why}\nDRAFT gaps: {gaps}\n"
        "Rewrite both to read naturally (why_fit ≤ 35 words, gaps ≤ 20 words), keeping every number verbatim.\n"
        'Return JSON: {"why_fit": "...", "gaps": "..."}'
    )
    try:
        out, _usage = provider.complete_json(_Explain, prompt, max_tokens=250, route="shortlist_explain")
    except Exception:  # noqa: BLE001
        return why, gaps, False
    text_ = f"{out.why_fit} {out.gaps}"
    if validate_digits(text_, f"{why} {gaps}"):
        return why, gaps, False
    # reject any skill-like token from the JD vocabulary that the facts don't support
    jd_vocab = {s.lower() for s in spec.required + spec.preferred}
    for skill in jd_vocab:
        if re.search(rf"(?<![a-z0-9]){re.escape(skill)}(?![a-z0-9])", text_.lower()) and skill not in allowed_skills:
            return why, gaps, False
    return out.why_fit.strip(), out.gaps.strip(), True


@dataclass
class ShortlistResult:
    spec: JdSpec
    pool_size: int
    candidates: list[dict[str, Any]]


def shortlist(
    db: Session,
    employer: User,
    jd_text: str,
    *,
    title: str | None = None,
    location: str | None = None,
    posting: Job | None = None,
    top_k: int = 10,
) -> ShortlistResult:
    spec = structure_jd(jd_text, title_hint=title, location=location)
    job = _transient_job(db, spec)

    applicants: dict[UUID, Application] = {}
    if posting is not None:
        for app_row in db.scalars(select(Application).where(Application.job_id == posting.id)).all():
            if app_row.profile_id is not None:
                applicants[app_row.profile_id] = app_row

    ids = _candidate_ids(db, job, spec, set(applicants), employer.id)
    if not ids:
        return ShortlistResult(spec=spec, pool_size=0, candidates=[])

    from app.services.recommender import _ProfileContext
    from app.services.scoring import score_pair

    profiles = db.scalars(
        select(Profile)
        .where(Profile.id.in_(ids))
        .options(
            selectinload(Profile.skills).selectinload(ProfileSkill.skill),
            selectinload(Profile.target_family),
            selectinload(Profile.user),
        )
    ).all()

    scored = []
    for profile in profiles:
        try:
            result = score_pair(_ProfileContext(profile).inputs_for(job))
        except Exception:  # noqa: BLE001 - one malformed profile never sinks the list
            log.debug("shortlist scoring failed for %s", profile.id, exc_info=True)
            continue
        scored.append((profile, result))
    scored.sort(key=lambda t: (-t[1].final, str(t[0].id)))

    out: list[dict[str, Any]] = []
    for rank, (profile, result) in enumerate(scored[:top_k], start=1):
        d = result.skill_detail
        years = profile.years_of_experience
        why, gaps = _template(spec, years, list(d.matched), list(d.missing_required), list(d.transferable))
        polished = False
        if rank <= LLM_EXPLAIN_TOP_N:
            facts = {
                "fit_score": round(result.final, 1),
                "years_experience": years,
                "matched": list(d.matched)[:8],
                "missing_required": list(d.missing_required)[:6],
                "transferable": list(d.transferable)[:4],
                "headline": profile.headline,
            }
            why, gaps, polished = _llm_explain(spec, facts, why, gaps)

        application = applicants.get(profile.id)
        first = (profile.first_name or "").strip()
        last = (profile.last_name or "").strip()
        name = f"{first} {last}".strip() if application else (f"{first} {last[:1]}." if last else first) or "Candidate"
        out.append(
            {
                "rank": rank,
                "profile_id": str(profile.id),
                "name": name,
                "headline": profile.headline,
                "location": profile.location,
                "years_of_experience": years,
                "fit_score": round(result.final, 1),
                "components": result.components,
                "matched_skills": list(d.matched),
                "missing_required": list(d.missing_required),
                "transferable": list(d.transferable),
                "why_fit": why,
                "gaps": gaps,
                "explanation_source": "llm" if polished else "template",
                "is_applicant": application is not None,
                "application_id": str(application.id) if application else None,
                "email": profile.user.email if application is not None and profile.user is not None else None,
            }
        )
    return ShortlistResult(spec=spec, pool_size=len(scored), candidates=out)
