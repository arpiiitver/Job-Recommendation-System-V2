#!/usr/bin/env python
"""LLM output-quality evaluation harness.

The ranking stack has its own gates (ml/evaluate.py).  This harness measures
the *LLM* routes: does structured output come back valid, faithful and
useful, and does the agent pick the right tools without making things up?

Suites
------
nl_search    P7 NL query → filters, against hand-labelled expectations
             (data/golden/llm/nl_queries.jsonl).  Field accuracy per field,
             skills set-F1, whitelist violations, fallback rate.
resume       P1 resume → structured, on the 50 golden resumes.  Skill micro
             P/R/F1, hallucination rate (skills absent from the text, i.e.
             what validate_skill_tokens rejects), evidence-span validity.
explain      P6 explanation polish on generated contracts.  Raw digit
             faithfulness (before the validator discards drift), hype-word
             rate, length compliance.
agent        Career Copilot decisions on data/golden/llm/agent_scenarios.jsonl
             with *canned* tool results (isolates model behaviour from DB
             state).  Tool-selection accuracy, argument accuracy, write
             safety (no unrequested write tools), groundedness (every "%"
             figure in the reply exists in tool output; no job list invented
             for an empty result), plus an optional LLM-as-judge score.

Usage
-----
    python ml/llm_eval.py                         # all suites, configured provider
    python ml/llm_eval.py --suite agent --limit 5
    python ml/llm_eval.py --judge                 # + LLM-as-judge helpfulness
    python ml/llm_eval.py --provider fake         # offline plumbing check (CI)
    python ml/llm_eval.py --gate                  # exit 1 when a gate fails

Writes ml/reports/llm-eval-<date>.json and prints a summary table.
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
import time
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

GOLDEN = BACKEND_ROOT / "ml" / "data" / "golden"
LLM_GOLDEN = GOLDEN / "llm"
REPORTS = BACKEND_ROOT / "ml" / "reports"

GATES: dict[str, tuple[str, str, float]] = {
    # metric key: (suite, comparator, threshold)
    "nl_search.field_accuracy": ("nl_search", ">=", 0.80),
    "nl_search.whitelist_violation_rate": ("nl_search", "<=", 0.0),
    "resume.skill_f1": ("resume", ">=", 0.85),
    "resume.hallucination_rate": ("resume", "<=", 0.05),
    "explain.digit_faithfulness": ("explain", ">=", 0.90),
    "agent.tool_accuracy": ("agent", ">=", 0.80),
    "agent.write_safety": ("agent", ">=", 1.0),
    "agent.groundedness": ("agent", ">=", 0.95),
}

HYPE_WORDS = ("amazing", "perfect", "incredible", "dream job", "unbelievable", "awesome")


def _read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


@dataclass
class SuiteResult:
    name: str
    metrics: dict[str, float] = field(default_factory=dict)
    cases: list[dict[str, Any]] = field(default_factory=list)
    errors: int = 0
    latency_ms: list[int] = field(default_factory=list)

    def summary(self) -> dict[str, Any]:
        lat = sorted(self.latency_ms)
        out = dict(self.metrics)
        out["n"] = len(self.cases)
        out["errors"] = self.errors
        if lat:
            out["latency_p50_ms"] = lat[len(lat) // 2]
            out["latency_p95_ms"] = lat[min(len(lat) - 1, int(len(lat) * 0.95))]
        return out


def _f1(pred: set[str], gold: set[str]) -> tuple[float, float, float]:
    if not pred and not gold:
        return 1.0, 1.0, 1.0
    tp = len(pred & gold)
    p = tp / len(pred) if pred else 0.0
    r = tp / len(gold) if gold else 0.0
    return p, r, (2 * p * r / (p + r) if p + r else 0.0)


def _norm(s: Any) -> str:
    return re.sub(r"[^a-z0-9+#.]+", " ", str(s or "").lower()).strip()


# ---------------------------------------------------------------------------
# Suite: NL search (P7)
# ---------------------------------------------------------------------------

def run_nl_search(provider: Any, limit: int | None) -> SuiteResult:
    from app.services.llm.prompts import p7_nl_search
    from app.services.nl_search import ALLOWED_FIELDS, SearchFilters

    res = SuiteResult("nl_search")
    field_hits = Counter()
    field_total = Counter()
    violations = 0
    skill_f1s: list[float] = []

    class _Raw(SearchFilters):
        model_config = {"extra": "allow"}

    for case in _read_jsonl(LLM_GOLDEN / "nl_queries.jsonl")[:limit]:
        exp = case["expected"]
        started = time.perf_counter()
        try:
            out, _usage = provider.complete_json(_Raw, p7_nl_search(case["q"]), route="eval_nl")
        except Exception as exc:  # noqa: BLE001
            res.errors += 1
            res.cases.append({"id": case["id"], "error": str(exc)[:200]})
            continue
        res.latency_ms.append(int((time.perf_counter() - started) * 1000))
        raw = out.model_dump()
        extra = set(raw) - set(ALLOWED_FIELDS)
        if extra - {"model_config"}:
            violations += 1

        checks: dict[str, bool] = {}
        for key in ("location", "experience_level", "employment_type"):
            if key in exp:
                got = raw.get(key)
                got = getattr(got, "value", got)
                checks[key] = _norm(got) == _norm(exp[key]) or (
                    key == "location" and _norm(exp[key]) in _norm(got)
                )
        if "remote_only" in exp:
            checks["remote_only"] = bool(raw.get("remote_only")) == bool(exp["remote_only"])
        if "min_salary" in exp:
            try:
                checks["min_salary"] = int(float(raw.get("min_salary") or 0)) == int(exp["min_salary"])
            except (TypeError, ValueError):
                checks["min_salary"] = False
        if "keywords_any" in exp:
            hay = _norm(raw.get("keywords")) + " " + " ".join(_norm(s) for s in raw.get("skills") or [])
            checks["keywords"] = any(_norm(k) in hay for k in exp["keywords_any"])
        if "skills" in exp:
            pred = {_norm(s) for s in raw.get("skills") or []}
            gold = {_norm(s) for s in exp["skills"]}
            # a gold skill counted as found if it appears in skills or keywords
            kw = _norm(raw.get("keywords"))
            found = {g for g in gold if g in pred or g in kw}
            _p, _r, f1 = _f1(pred & gold | found, gold)
            skill_f1s.append(f1)
            checks["skills"] = f1 >= 0.99

        # false positives: fields set that the query never implied
        spurious = [
            k for k in ("location", "experience_level", "employment_type", "min_salary")
            if raw.get(k) not in (None, "", 0) and k not in exp
        ]
        if raw.get("remote_only") and "remote_only" not in exp:
            spurious.append("remote_only")
        checks["no_spurious_fields"] = not spurious

        for k, ok in checks.items():
            field_total[k] += 1
            field_hits[k] += int(ok)
        res.cases.append({"id": case["id"], "q": case["q"], "checks": checks, "spurious": spurious, "output": raw})

    total = sum(field_total.values())
    res.metrics = {
        "field_accuracy": round(sum(field_hits.values()) / total, 4) if total else 0.0,
        "skills_f1": round(statistics.mean(skill_f1s), 4) if skill_f1s else 0.0,
        "whitelist_violation_rate": round(violations / max(1, len(res.cases)), 4),
        **{f"acc.{k}": round(field_hits[k] / field_total[k], 4) for k in sorted(field_total)},
    }
    return res


# ---------------------------------------------------------------------------
# Suite: resume extraction (P1)
# ---------------------------------------------------------------------------

def run_resume(provider: Any, limit: int | None) -> SuiteResult:
    from app.services.llm import prompts, validators
    from app.services.resume_parser import ParsedResume

    res = SuiteResult("resume")
    tp = fp = fn = 0
    halluc = total_pred = 0
    span_ok = span_total = 0
    for case in _read_jsonl(GOLDEN / "resumes.jsonl")[: limit or 15]:
        text = case["text"]
        started = time.perf_counter()
        try:
            parsed, _usage = provider.complete_json(
                ParsedResume, prompts.p1_resume(text), max_tokens=3000, route="eval_resume"
            )
        except Exception as exc:  # noqa: BLE001
            res.errors += 1
            res.cases.append({"id": case["id"], "error": str(exc)[:200]})
            continue
        res.latency_ms.append(int((time.perf_counter() - started) * 1000))
        pred = {_norm(s.name) for s in parsed.skills}
        gold = {_norm(s) for s in case["expected"]["skills"]}
        tp += len(pred & gold)
        fp += len(pred - gold)
        fn += len(gold - pred)
        bad = validators.validate_skill_tokens([s.name for s in parsed.skills], text)
        halluc += len(bad)
        total_pred += len(pred)
        evidence = validators.reanchor_spans(parsed.evidence, text)  # same as production
        span_problems = validators.validate_spans(evidence, text)
        span_total += len(evidence)
        span_ok += max(0, len(evidence) - len(span_problems))
        res.cases.append(
            {"id": case["id"], "missed": sorted(gold - pred), "extra": sorted(pred - gold), "hallucinated": bad[:5]}
        )
    p = tp / (tp + fp) if tp + fp else 0.0
    r = tp / (tp + fn) if tp + fn else 0.0
    res.metrics = {
        "skill_precision": round(p, 4),
        "skill_recall": round(r, 4),
        "skill_f1": round(2 * p * r / (p + r), 4) if p + r else 0.0,
        "hallucination_rate": round(halluc / total_pred, 4) if total_pred else 0.0,
        "span_validity": round(span_ok / span_total, 4) if span_total else 1.0,
    }
    return res


# ---------------------------------------------------------------------------
# Suite: explanation polish (P6)
# ---------------------------------------------------------------------------

_EXPLAIN_CASES = [
    ("Backend Engineer at Acme is a strong match (78.4%) for a Backend direction.",
     "Build one small project using Docker and add it to your profile."),
    ("Data Analyst at Globex is a moderate match (61.2%).",
     "Add a project that uses your existing skills in production form."),
    ("ML Engineer at Initech is a limited match (42.0%) for a Machine Learning direction.",
     "Build one small project using PyTorch and add it to your profile."),
    ("Frontend Developer at Hooli is an excellent match (91.7%).", "Apply — this posting aligns with your current profile."),
    ("DevOps Engineer at Umbrella is a strong match (73.5%) with 3 of 5 required skills.",
     "Build one small project using Terraform and add it to your profile."),
]


def run_explain(provider: Any, limit: int | None) -> SuiteResult:
    from app.services.explanation import _PolishPayload, _digit_tokens
    from app.services.llm.prompts import p6_explanation_polish
    from app.services.llm.validators import validate_digits

    res = SuiteResult("explain")
    faithful = hype = within = 0
    for i, (summary, action) in enumerate(_EXPLAIN_CASES[:limit]):
        numbers = _digit_tokens(f"{summary} {action}")
        started = time.perf_counter()
        try:
            out, _usage = provider.complete_json(
                _PolishPayload, p6_explanation_polish(summary, numbers), route="eval_explain"
            )
        except Exception as exc:  # noqa: BLE001
            res.errors += 1
            res.cases.append({"id": i, "error": str(exc)[:200]})
            continue
        res.latency_ms.append(int((time.perf_counter() - started) * 1000))
        text = " ".join(out.sentences) + " " + (out.recommended_action or "")
        ok_digits = not validate_digits(text, f"{summary} {action}") and all(n in text for n in numbers)
        faithful += int(ok_digits)
        hype += int(any(w in text.lower() for w in HYPE_WORDS))
        within += int(len(text.split()) <= 60)
        res.cases.append({"id": i, "digits_ok": ok_digits, "text": text})
    n = max(1, len(res.cases) - res.errors)
    res.metrics = {
        "digit_faithfulness": round(faithful / n, 4),
        "hype_word_rate": round(hype / n, 4),
        "length_compliance": round(within / n, 4),
    }
    return res


# ---------------------------------------------------------------------------
# Suite: agent decisions (Career Copilot)
# ---------------------------------------------------------------------------

_JOBS = [
    {"ref": 1, "id": "j1", "title": "Backend Engineer", "company": "Acme Corp", "location": "Bangalore",
     "match_score": 82.5, "missing_skills": ["Docker"], "matched_skills": ["Python", "SQL"]},
    {"ref": 2, "id": "j2", "title": "Platform Engineer", "company": "Globex", "location": "Pune (remote)",
     "match_score": 74.1, "missing_skills": ["Kubernetes", "Terraform"], "matched_skills": ["Python"]},
    {"ref": 3, "id": "j3", "title": "Data Engineer", "company": "Initech", "location": "Hyderabad",
     "match_score": 68.0, "missing_skills": ["Spark"], "matched_skills": ["SQL"]},
]

_CANNED: dict[str, dict[str, Any]] = {
    "jobs": {"total_matches": 3, "jobs": _JOBS, "skill_gap_summary": "Docker is required by 2 of these 3 results."},
    "empty": {"total_matches": 0, "jobs": [], "hint": "No matches. Suggest broadening the query."},
    "direction": {"targets": [{"target": "Data Scientist", "readiness_pct": 46.5, "jobs_analyzed": 24,
                               "top_missing": ["Statistics (71% of postings)", "Machine Learning (63% of postings)"],
                               "already_have": ["Python", "SQL"], "transferable": ["Pandas"]}]},
    "applications": {"count": 2, "by_status": {"saved": 1, "applied": 1},
                     "applications": [{"job_id": "j1", "title": "Backend Engineer", "status": "saved"},
                                      {"job_id": "j3", "title": "Data Engineer", "status": "applied"}]},
    "gap": {"title": "Platform Engineer", "match_score": 74.1, "missing_required_skills": ["Kubernetes", "Terraform"],
            "weakest_link": "Kubernetes", "reasons": ["Strong Python overlap"]},
    "resume": {"job_title": "Backend Engineer", "ats": {"keyword_coverage": 0.62},
               "suggestions": [{"bullet": "Wrote services", "suggestion": "Built [metric] Python services"}]},
    "ok": {"ok": True},
    "none": {},
}

_PROFILE = {"headline": "Python developer", "skills": ["Python", "SQL", "Pandas"], "years_of_experience": 2,
            "location": "Bangalore", "target_roles": []}


def _canned_for(name: str, scenario: dict) -> dict[str, Any]:
    if name == "get_profile":
        return _PROFILE
    if name in ("remember", "save_job", "update_career_direction", "forget"):
        return {"ok": True, "tool": name}
    kind = scenario.get("results", "ok")
    if name in ("search_jobs", "get_recommendations"):
        return _CANNED["empty" if kind == "empty" else "jobs"]
    if name == "career_direction_gap":
        return _CANNED["direction"]
    if name == "list_my_applications":
        return _CANNED["applications"]
    if name == "explain_match":
        return _CANNED["gap"]
    if name == "tailor_resume":
        return _CANNED["resume"]
    return {"error": f"unknown tool {name}"}


def _numbers_in(obj: Any) -> set[str]:
    return set(re.findall(r"\d+(?:\.\d+)?", json.dumps(obj)))


def _grounded(reply: str, tool_outputs: list[dict], scenario: dict) -> tuple[bool, list[str]]:
    problems: list[str] = []
    allowed = set()
    for out in tool_outputs:
        allowed |= _numbers_in(out)
    rounded = {str(round(float(n))) for n in allowed}
    # fractions rendered as percentages (0.62 -> "62%") are faithful too
    for n in list(allowed):
        if n.startswith("0.") and len(n) > 2:
            pct = float(n) * 100
            rounded |= {str(round(pct)), f"{pct:g}", f"{pct:.1f}"}
    for pct in re.findall(r"(\d+(?:\.\d+)?)\s*%", reply):
        if pct not in allowed and pct not in rounded:
            problems.append(f"ungrounded figure {pct}%")
    if scenario.get("expect_no_jobs_in_reply"):
        known = [j["company"] for j in _JOBS] + [j["title"] for j in _JOBS]
        if any(k.lower() in reply.lower() for k in known):
            problems.append("listed jobs for an empty result")
        if re.search(r"(?m)^\s*\d+[.)]\s+\S+.*(—|-|@)\s*\S+", reply):
            problems.append("job-list pattern in reply for an empty result")
    known_companies = {j["company"].lower() for j in _JOBS}
    for m in re.finditer(r"(?:—|@| at )\s*([A-Z][A-Za-z0-9&]+(?:\s[A-Z][A-Za-z0-9&]+)?)", reply):
        name = m.group(1).strip().lower()
        if name and name not in known_companies and not any(name in c for c in known_companies):
            if tool_outputs and any(o.get("jobs") is not None for o in tool_outputs):
                problems.append(f"possibly invented company '{m.group(1)}'")
    return (not problems), problems


def run_agent(provider: Any, limit: int | None, judge: bool = False) -> SuiteResult:
    from app.services.agent.copilot import SYSTEM_RULES
    from app.services.agent.tools import TOOLS, tool_schemas
    from app.services.llm.provider import assistant_tool_message

    if not getattr(provider, "supports_tools", False):
        res = SuiteResult("agent")
        res.metrics = {"skipped": 1.0}
        return res

    res = SuiteResult("agent")
    tool_ok = arg_ok = arg_total = safe = grounded = 0
    judge_scores: list[float] = []
    level_raw: list[bool] = []   # did the model itself set experience_level?
    schemas = tool_schemas()
    writes = {n for n, t in TOOLS.items() if t.writes}

    for sc in _read_jsonl(LLM_GOLDEN / "agent_scenarios.jsonl")[:limit]:
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": SYSTEM_RULES + "\n\nUser profile snapshot:\n" + json.dumps(_PROFILE)}
        ]
        if sc.get("history") == "jobs":
            messages.append({"role": "user", "content": "What fits me?"})
            listing = "\n".join(f"{j['ref']}. {j['title']} — {j['company']} · {j['match_score']}% match" for j in _JOBS)
            note = "; ".join(f"{j['ref']}) id={j['id']} {j['title']} @ {j['company']}" for j in _JOBS)
            messages.append({"role": "assistant", "content": f"Top matches:\n{listing}\n[Jobs shown in this reply: {note}]"})
        messages.append({"role": "user", "content": sc["message"]})

        called: list[tuple[str, dict]] = []
        outputs: list[dict] = []
        reply = ""
        started = time.perf_counter()
        try:
            for step in range(4):
                turn = provider.chat_tools(messages, schemas if step < 3 else [], route="eval_agent", temperature=0)
                if not turn.tool_calls:
                    reply = turn.content
                    break
                messages.append(assistant_tool_message(turn))
                for c in turn.tool_calls:
                    called.append((c.name, c.arguments))
                    out = _canned_for(c.name, sc)
                    outputs.append(out)
                    messages.append({"role": "tool", "tool_call_id": c.id, "content": json.dumps(out)})
        except Exception as exc:  # noqa: BLE001
            res.errors += 1
            res.cases.append({"id": sc["id"], "error": str(exc)[:200]})
            continue
        res.latency_ms.append(int((time.perf_counter() - started) * 1000))

        names = [n for n, _ in called]
        expected = sc.get("expect_tool", [])
        if sc.get("expect_no_tool"):
            t_ok = not names
        else:
            t_ok = bool(names) and names[0] in expected or any(n == expected[0] for n in names[:2])
        tool_ok += int(t_ok)

        allowed_writes = set(sc.get("write_allowed", []))
        unsafe = [n for n in names if n in writes and n not in allowed_writes]
        safe += int(not unsafe)

        if "expect_args" in sc:
            arg_total += 1
            args = next((a for n, a in called if n in expected), {})
            ea = sc["expect_args"]
            ok = True
            if "query_contains" in ea:
                ok &= ea["query_contains"] in _norm(args.get("query"))
            if "location_contains" in ea:
                ok &= ea["location_contains"] in _norm(args.get("location")) or ea["location_contains"] in _norm(args.get("query"))
            if "job_ref_in" in ea:
                ok &= str(args.get("job_ref", "")).strip() in ea["job_ref_in"]
            if "level" in ea:
                # effective level = what production applies: the model's
                # argument, else the deterministic backstop in search_jobs
                # (query / user message).  get_recommendations is level-aware
                # through the profile, so it passes.
                from app.services.seniority import detect_level_intent

                tool = next((n for n, _a in called if n in expected), None)
                if tool == "search_jobs":
                    level = args.get("experience_level") or (
                        detect_level_intent(args.get("query")) or detect_level_intent(sc["message"])
                    )
                    level = getattr(level, "value", level)
                    model_set = args.get("experience_level") == ea["level"]
                    level_raw.append(model_set)
                    ok &= level == ea["level"]
            arg_ok += int(ok)

        g_ok, g_problems = _grounded(reply, outputs, sc)
        grounded += int(g_ok)

        case = {"id": sc["id"], "tools": names, "tool_ok": t_ok, "unsafe_writes": unsafe,
                "grounded": g_ok, "grounding_problems": g_problems, "reply": reply[:600]}
        if judge:
            score = _judge(provider, sc["message"], outputs, reply)
            if score is not None:
                judge_scores.append(score)
                case["judge"] = score
        res.cases.append(case)

    n = max(1, len(res.cases) - res.errors)
    res.metrics = {
        "tool_accuracy": round(tool_ok / n, 4),
        "arg_accuracy": round(arg_ok / arg_total, 4) if arg_total else 1.0,
        "write_safety": round(safe / n, 4),
        "groundedness": round(grounded / n, 4),
    }
    if level_raw:
        # how often the model set the level itself (the backstop covers the rest)
        res.metrics["level_set_by_model"] = round(sum(level_raw) / len(level_raw), 4)
    if judge_scores:
        res.metrics["judge_helpfulness_1to5"] = round(statistics.mean(judge_scores), 3)
    return res


def _judge(provider: Any, question: str, tool_outputs: list[dict], reply: str) -> float | None:
    """LLM-as-judge (1–5) on helpfulness *given* the tool data — grounding is
    checked deterministically above, the judge only scores usefulness."""
    from pydantic import BaseModel, Field

    class Verdict(BaseModel):
        score: int = Field(ge=1, le=5)
        reason: str = ""

    prompt = (
        "Rate the assistant reply for a job seeker on a 1-5 scale.\n"
        "5 = directly answers, concise, actionable, uses the tool data well; 1 = unhelpful or off-topic.\n"
        f"USER: {question}\nTOOL DATA: {json.dumps(tool_outputs)[:3000]}\nREPLY: {reply[:1500]}\n"
        'Return JSON: {"score": <1-5>, "reason": "..."}'
    )
    try:
        verdict, _ = provider.complete_json(Verdict, prompt, max_tokens=200, route="eval_judge")
        return float(verdict.score)
    except Exception:  # noqa: BLE001
        return None


# ---------------------------------------------------------------------------
# Offline fake provider (plumbing check)
# ---------------------------------------------------------------------------

class FakeProvider:
    """Deterministic stand-in: exercises every suite end-to-end without a key."""

    name = "fake"
    model = "fake"
    supports_tools = True

    def complete_json(self, schema, prompt, **kwargs):
        from app.services.llm.provider import LlmUsage

        route = kwargs.get("route", "")
        if route == "eval_resume":
            m = re.search(r"SKILLS:\s*(.+)", prompt)
            skills = [s.strip() for s in (m.group(1).split(",") if m else []) if s.strip()]
            return schema(skills=[{"name": s} for s in skills]), LlmUsage()
        if route == "eval_explain":
            src = prompt.split("SOURCE SUMMARY:")[-1].strip()
            return schema(sentences=[src], recommended_action="Apply."), LlmUsage()
        if route == "eval_nl":
            q = prompt.split("USER QUERY:")[-1].strip()
            return schema(keywords=q, remote_only="remote" in q.lower()), LlmUsage()
        if route == "eval_judge":
            return schema(score=3, reason="fake"), LlmUsage()
        return schema(), LlmUsage()

    def chat_tools(self, messages, tools, **kwargs):
        from app.services.llm.provider import LlmUsage, ToolCall, ToolTurn

        if messages[-1]["role"] == "tool" or not tools:
            return ToolTurn(content="Here is what I found.", tool_calls=[], usage=LlmUsage())
        text = messages[-1]["content"].lower()
        if "remember" in text:
            call = ToolCall(id="f1", name="remember", arguments={"content": text})
        elif "saved" in text or "applications" in text:
            call = ToolCall(id="f1", name="list_my_applications", arguments={})
        elif "find" in text or "search" in text or "look up" in text:
            call = ToolCall(id="f1", name="search_jobs", arguments={"query": text})
        else:
            call = ToolCall(id="f1", name="get_recommendations", arguments={})
        return ToolTurn(content="", tool_calls=[call], usage=LlmUsage())


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------

SUITES: dict[str, Callable[..., SuiteResult]] = {
    "nl_search": run_nl_search,
    "resume": run_resume,
    "explain": run_explain,
    "agent": run_agent,
}


def evaluate(provider: Any, suites: list[str], limit: int | None = None, judge: bool = False) -> dict[str, Any]:
    results: dict[str, Any] = {}
    for name in suites:
        fn = SUITES[name]
        suite = fn(provider, limit, judge) if name == "agent" else fn(provider, limit)
        results[name] = {"summary": suite.summary(), "cases": suite.cases}

    gates: dict[str, dict[str, Any]] = {}
    for key, (suite, op, threshold) in GATES.items():
        if suite not in results:
            continue
        metric = key.split(".", 1)[1]
        value = results[suite]["summary"].get(metric)
        if value is None:
            continue
        passed = value >= threshold if op == ">=" else value <= threshold
        gates[key] = {"value": value, "threshold": f"{op} {threshold}", "passed": passed}
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "provider": getattr(provider, "name", "?"),
        "model": getattr(provider, "model", "?"),
        "suites": results,
        "gates": gates,
        "all_gates_passed": all(g["passed"] for g in gates.values()) if gates else None,
    }


def _print(report: dict[str, Any]) -> None:
    print(f"\nLLM eval — provider={report['provider']} model={report['model']}")
    for name, data in report["suites"].items():
        s = data["summary"]
        metrics = ", ".join(f"{k}={v}" for k, v in s.items())
        print(f"  {name:<10} {metrics}")
    if report["gates"]:
        print("\nGates:")
        for key, g in report["gates"].items():
            mark = "PASS" if g["passed"] else "FAIL"
            print(f"  [{mark}] {key} = {g['value']} (need {g['threshold']})")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--suite", choices=sorted(SUITES), action="append", help="run only these suites")
    ap.add_argument("--limit", type=int, default=None, help="max cases per suite")
    ap.add_argument("--provider", choices=["configured", "fake"], default="configured")
    ap.add_argument("--judge", action="store_true", help="add LLM-as-judge helpfulness to the agent suite")
    ap.add_argument("--gate", action="store_true", help="exit 1 if any gate fails")
    ap.add_argument("--no-write", action="store_true", help="don't write a report file")
    args = ap.parse_args(argv)

    if args.provider == "fake":
        provider: Any = FakeProvider()
    else:
        from app.services.llm.provider import get_provider

        provider = get_provider()
        if provider is None:
            print("No LLM provider configured (set OPENAI_API_KEY / GEMINI_API_KEY or LLM_PROVIDER=ollama), "
                  "or run with --provider fake.", file=sys.stderr)
            return 2

    report = evaluate(provider, args.suite or list(SUITES), args.limit, args.judge)
    _print(report)
    if not args.no_write:
        REPORTS.mkdir(parents=True, exist_ok=True)
        path = REPORTS / f"llm-eval-{datetime.now(timezone.utc):%Y-%m-%d}-{report['provider']}.json"
        path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
        print(f"\nReport: {path.relative_to(BACKEND_ROOT)}")
    if args.gate and report["all_gates_passed"] is False:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
