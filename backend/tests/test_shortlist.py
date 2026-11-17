"""AI talent shortlist: opt-in pool, privacy, deterministic ranking, structuring."""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from app.core.db import SessionLocal
from app.main import app
from app.models.user import User
from app.services.talent_shortlist import structure_jd

client = TestClient(app, raise_server_exceptions=False)

JD = """Backend Engineer (Python)
We are hiring a backend engineer with 3+ years of experience.
Requirements: Python, FastAPI, PostgreSQL and Docker. Nice to have: Kubernetes.
You will design APIs and own services in production."""


def _register(role: str, tag: str) -> dict:
    uid = uuid4().hex[:8]
    reg = client.post(
        "/api/v1/auth/register",
        json={"email": f"shortlist_{tag}_{uid}@example.com", "password": "Password123!", "role": role},
    )
    assert reg.status_code == 201, reg.text
    body = reg.json()
    return {"headers": {"Authorization": f"Bearer {body['access_token']}"}, "id": body["user"]["id"], "email": body["user"]["email"]}


def _delete(*ids) -> None:
    with SessionLocal() as db:
        for uid in ids:
            row = db.get(User, UUID(str(uid)))
            if row is not None:
                db.delete(row)
        db.commit()


def _profile(acct: dict, *, first: str, last: str, skills: list[str], open_: bool, years: float) -> None:
    r = client.put(
        "/api/v1/profiles/me",
        headers=acct["headers"],
        json={
            "first_name": first,
            "last_name": last,
            "headline": "Backend developer",
            "years_of_experience": years,
            "skills": [{"skill_name": s} for s in skills],
            "open_to_recruiters": open_,
        },
    )
    assert r.status_code == 200, r.text
    assert r.json()["open_to_recruiters"] is open_


@pytest.fixture
def world():
    emp = _register("employer", "emp")
    strong = _register("job_seeker", "strong")
    weak = _register("job_seeker", "weak")
    hidden = _register("job_seeker", "hidden")
    tag = uuid4().hex[:6]
    _profile(strong, first="Asha", last=f"Strong{tag}", skills=["Python", "FastAPI", "PostgreSQL", "Docker"], open_=True, years=4)
    _profile(weak, first="Ravi", last=f"Weak{tag}", skills=["Python"], open_=True, years=1)
    _profile(hidden, first="Hidden", last=f"Person{tag}", skills=["Python", "FastAPI", "PostgreSQL", "Docker"], open_=False, years=5)
    yield {"emp": emp, "strong": strong, "weak": weak, "hidden": hidden, "tag": tag}
    _delete(emp["id"], strong["id"], weak["id"], hidden["id"])


def test_structure_jd_rules_only():
    spec = structure_jd(JD)
    req = {s.lower() for s in spec.required + spec.preferred}
    assert {"python", "fastapi", "postgresql"} <= req
    assert spec.years_min == 3.0
    assert spec.structured_by == "rules"   # conftest: no provider


def test_shortlist_ranks_opted_in_only_with_privacy(world):
    r = client.post("/api/v1/employer/shortlist", headers=world["emp"]["headers"], json={"jd_text": JD, "top_k": 30})
    assert r.status_code == 200, r.text
    body = r.json()
    tag = world["tag"]
    ours = [c for c in body["candidates"] if tag in (c["name"] or "") or c["name"].startswith(("Asha", "Ravi", "Hidden"))]
    names = [c["name"] for c in ours]
    assert any(n.startswith("Asha") for n in names)
    assert not any(n.startswith("Hidden") for n in names), "opted-out profile leaked"

    asha = next(c for c in ours if c["name"].startswith("Asha"))
    assert asha["name"] == "Asha S."          # non-applicant → last initial only
    assert asha["email"] is None and asha["is_applicant"] is False
    assert asha["why_fit"] and asha["gaps"]
    assert asha["explanation_source"] == "template"

    ravi = next((c for c in ours if c["name"].startswith("Ravi")), None)
    if ravi is not None:
        assert asha["fit_score"] > ravi["fit_score"]
        assert asha["rank"] < ravi["rank"]


def test_shortlist_validation_and_roles(world):
    r = client.post("/api/v1/employer/shortlist", headers=world["emp"]["headers"], json={"jd_text": "too short"})
    assert r.status_code == 422
    r = client.post("/api/v1/employer/shortlist", headers=world["strong"]["headers"], json={"jd_text": JD})
    assert r.status_code == 403
