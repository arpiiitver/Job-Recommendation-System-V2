"""Skill disparity across result sets + career-direction (switcher) signals.

Pure-function tests pin the math; two API tests pin the contracts the UI
banners rely on (`skill_gap_summary` on the feed/search, `/careers/direction-gap`).
"""

from __future__ import annotations

from types import SimpleNamespace
from uuid import UUID, uuid4

from fastapi.testclient import TestClient

from app.core.db import SessionLocal
from app.main import app
from app.models.user import User
from app.services.personalization import apply_direction_shift
from app.services.scoring import (
    DEFAULT_WEIGHTS,
    NEUTRAL_INTEREST,
    ComponentInputs,
    JobSkillReq,
    interest_match,
    role_alignment,
)
from app.services.skill_disparity import gap_for_requirements, summarize_items
from app.services.skill_graph import load_seed_graph

client = TestClient(app, raise_server_exceptions=False)


# --------------------------------------------------------------------------- #
# role_alignment / interest_match
# --------------------------------------------------------------------------- #

def _inputs(**kw) -> ComponentInputs:
    return ComponentInputs(**kw)


def test_target_role_matches_title_strongly():
    hit = role_alignment(_inputs(seeker_target_roles=["Data Scientist"], job_title="Senior Data Scientist"))
    assert hit == (100.0, "Data Scientist")


def test_close_role_wording_still_matches():
    hit = role_alignment(_inputs(seeker_target_roles=["Data Science"], job_title="Data Scientist"))
    assert hit is not None and hit[0] >= 85.0


def test_domain_found_in_job_text():
    hit = role_alignment(
        _inputs(
            seeker_target_roles=["Fintech"],
            job_title="Backend Engineer",
            job_text="backend engineer building payments for a fintech startup",
        )
    )
    assert hit is not None and hit[0] == 75.0


def test_domain_requires_whole_word():
    hit = role_alignment(
        _inputs(seeker_target_roles=["ai"], job_title="Sales Rep", job_text="maintain retail accounts")
    )
    assert hit is None


def test_typed_skill_counts_as_interest_hit():
    hit = role_alignment(
        _inputs(
            seeker_target_roles=["kubernetes"],
            job_title="Platform Engineer",
            job_skills=[JobSkillReq(name="Kubernetes")],
        )
    )
    assert hit is not None and hit[0] == 80.0


def test_unmatched_roles_never_penalise():
    """Regression: roles used to be Jaccard'ed against skills → 0 for every job."""
    score = interest_match(
        _inputs(
            seeker_target_roles=["Product Manager"],
            job_title="Java Developer",
            job_skills=[JobSkillReq(name="Java")],
        )
    )
    assert score == NEUTRAL_INTEREST


def test_family_and_role_take_the_max():
    score = interest_match(
        _inputs(
            seeker_target_family="Data Science",
            job_family="Data Science",
            seeker_target_roles=["fintech"],
            job_title="Data Scientist",
        )
    )
    assert score == 100.0


def test_alert_skill_overlap_path_unchanged():
    # job alerts pass a saved job's skills as interest skills (no roles)
    score = interest_match(
        _inputs(
            seeker_interest_skills={"python", "sql"},
            job_skills=[JobSkillReq(name="Python"), JobSkillReq(name="Go")],
        )
    )
    assert round(score, 2) == round(100 / 3, 2)


# --------------------------------------------------------------------------- #
# direction weight shift
# --------------------------------------------------------------------------- #

def test_direction_shift_moves_experience_to_interest():
    w, note = apply_direction_shift(dict(DEFAULT_WEIGHTS), has_direction=True)
    assert note
    assert abs(sum(w.values()) - 1.0) < 1e-9
    assert w["interest"] > DEFAULT_WEIGHTS["interest"]
    assert w["experience"] < DEFAULT_WEIGHTS["experience"]
    assert w["experience"] >= 0.05 - 1e-9


def test_direction_shift_noop_without_direction():
    w, note = apply_direction_shift(dict(DEFAULT_WEIGHTS), has_direction=False)
    assert note is None and w == DEFAULT_WEIGHTS


# --------------------------------------------------------------------------- #
# result-set summary
# --------------------------------------------------------------------------- #

def _card(matched=(), missing=(), transferable=(), score=None):
    return SimpleNamespace(
        matched_skills=list(matched),
        missing_skills=list(missing),
        transferable_skills=list(transferable),
        skill_match_score=score,
    )


def test_summary_counts_and_ranks_missing():
    cards = [
        _card(matched=["Python"], missing=["Docker", "AWS"], score=50),
        _card(matched=["Python"], missing=["Docker"], score=70),
        _card(missing=["Docker", "Kubernetes"], transferable=["Kubernetes"], score=30),
        _card(),  # unannotated card (no skills listed) → not analysed
    ]
    s = summarize_items(cards)
    assert s is not None
    assert s.jobs_analyzed == 3
    assert s.avg_skill_match == 50.0
    assert s.top_missing[0].skill == "Docker" and s.top_missing[0].jobs == 3
    assert s.top_missing[0].share == 1.0
    assert s.top_matched[0].skill == "Python" and s.top_matched[0].jobs == 2
    assert s.top_transferable[0].skill == "Kubernetes"
    assert "Docker is required by 3 of these 3 results" in (s.headline or "")


def test_summary_none_when_nothing_annotated():
    assert summarize_items([_card(), _card()]) is None
    assert summarize_items([]) is None


def test_summary_headline_when_fully_covered():
    s = summarize_items([_card(matched=["Python"], score=100)])
    assert s is not None and s.top_missing == []
    assert "cover the required skills" in (s.headline or "")


# --------------------------------------------------------------------------- #
# direction gap aggregation (pure)
# --------------------------------------------------------------------------- #

def test_direction_gap_classifies_and_scores_readiness():
    reqs = [
        ({"title": "Data Scientist"}, [JobSkillReq(name="Python"), JobSkillReq(name="Statistics")]),
        ({"title": "ML Scientist"}, [JobSkillReq(name="Python"), JobSkillReq(name="Statistics")]),
        ({"title": "Analyst"}, [JobSkillReq(name="Python")]),
        ({"title": "Empty"}, []),  # no listed skills → ignored
    ]
    gap = gap_for_requirements(
        "Data Scientist",
        "role",
        reqs,
        {"python": (None, 3.0)},
        graph=load_seed_graph(),
        sources=["platform"],
    )
    assert gap.jobs_analyzed == 3
    by = {s.skill: s for s in gap.skills}
    assert by["Python"].status == "have" and by["Python"].share == 1.0
    assert by["Statistics"].status in ("missing", "transferable")
    assert gap.readiness is not None and 0 < gap.readiness < 100
    assert len(gap.sample_jobs) == 3


def test_direction_gap_empty_has_note():
    gap = gap_for_requirements("Astronaut", "role", [], {}, graph=load_seed_graph(), sources=[])
    assert gap.jobs_analyzed == 0 and gap.readiness is None and gap.note


# --------------------------------------------------------------------------- #
# API contracts
# --------------------------------------------------------------------------- #

def _register() -> dict:
    uid = uuid4().hex[:8]
    reg = client.post(
        "/api/v1/auth/register",
        json={"email": f"disparity_{uid}@example.com", "password": "Password123!", "role": "job_seeker"},
    )
    assert reg.status_code == 201, reg.text
    body = reg.json()
    return {"headers": {"Authorization": f"Bearer {body['access_token']}"}, "id": body["user"]["id"]}


def _delete(user_id) -> None:
    with SessionLocal() as db:
        row = db.get(User, UUID(str(user_id)))
        if row is not None:
            db.delete(row)
            db.commit()


def test_direction_gap_endpoint_contract():
    acct = _register()
    try:
        empty = client.get("/api/v1/careers/direction-gap?live=false", headers=acct["headers"])
        assert empty.status_code == 200, empty.text
        assert empty.json()["targets"] == [] and empty.json()["message"]

        put = client.put(
            "/api/v1/profiles/me",
            headers=acct["headers"],
            json={"career_interests": ["Data Scientist", "Fintech"]},
        )
        assert put.status_code == 200, put.text

        resp = client.get("/api/v1/careers/direction-gap?live=false", headers=acct["headers"])
        assert resp.status_code == 200, resp.text
        targets = resp.json()["targets"]
        assert [t["target"] for t in targets] == ["Data Scientist", "Fintech"]
        for t in targets:
            assert t["kind"] == "role"
            assert "live" not in t["sources"]
    finally:
        _delete(acct["id"])


def test_feed_carries_skill_gap_summary_field():
    acct = _register()
    try:
        resp = client.get("/api/v1/recommendations?limit=5", headers=acct["headers"])
        assert resp.status_code == 200, resp.text
        assert "skill_gap_summary" in resp.json()
    finally:
        _delete(acct["id"])
