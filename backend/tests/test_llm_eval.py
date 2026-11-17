"""LLM eval harness plumbing: every suite runs end-to-end on the fake provider,
and the gates discriminate (the deliberately naive fake must fail some)."""

from ml.llm_eval import FakeProvider, _grounded, evaluate


def test_harness_runs_all_suites_offline():
    report = evaluate(FakeProvider(), ["nl_search", "resume", "explain", "agent"], limit=5)
    assert set(report["suites"]) == {"nl_search", "resume", "explain", "agent"}
    for suite in report["suites"].values():
        assert suite["summary"]["errors"] == 0
    assert report["gates"]["agent.write_safety"]["passed"] is True


def test_groundedness_catches_invented_figures_and_jobs():
    tool_out = [{"jobs": [{"title": "Backend Engineer", "company": "Acme Corp", "match_score": 82.5}]}]
    ok, _ = _grounded("1. Backend Engineer — Acme Corp · 82.5% match", tool_out, {})
    assert ok
    bad, problems = _grounded("1. Backend Engineer — Acme Corp · 97% match", tool_out, {})
    assert not bad and any("97" in p for p in problems)
    empty, problems = _grounded(
        "1. Rust Dev — Acme Corp", [{"jobs": []}], {"expect_no_jobs_in_reply": True}
    )
    assert not empty
