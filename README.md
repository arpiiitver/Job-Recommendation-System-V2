# JobRec — AI-Powered Job Recommendation & Career Path Intelligence

Full-stack job recommendation platform: **Next.js frontend, FastAPI backend, PostgreSQL + pgvector**, with a deterministic 8-component scoring engine, five trained model artifacts (M1–M5), and an LLM layer that never assigns scores or executes SQL.

**Design:** the full technical design (data model, scoring engine, model suite, LLM layer, evaluation, file-by-file plan) lives in [`docs/DESIGN.md`](docs/DESIGN.md). The 25-point original plan maps 1:1 to its §1 traceability table.

## Architecture

```
frontend (Next.js 16, App Router, 30 routes)
   │  fetch / JWT
backend (FastAPI, /api/v1, 87 OpenAPI paths)
   ├─ deterministic core   scoring.py · gap engine · roadmap · simulator · project mapping
   ├─ personalization      M4 weight learner + skill affinities · M5 onboarding elicitation
   ├─ search               PostgreSQL FTS + pgvector KNN + skill overlap (RRF fusion) · NL filter parser
   ├─ LLM layer            one OpenAI-compatible provider path (OpenAI / Gemini / Ollama)
   │                       JSON mode + native tool calling + retries/backoff
   │                       prompts P1–P10, all schema-validated with deterministic fallbacks
   ├─ Career Copilot       tool-using agent (11 tools over the real services) +
   │                       long-term memory (pgvector recall) + rolling summary
   ├─ AI shortlist         JD → structured requirements → opt-in candidate retrieval
   │                       (KNN ∪ skill overlap, RRF) → same 8-component engine → grounded notes
   └─ ml/                  M1 skill2vec · M2 LightGBM ranklet · M3 isotonic calibrator
                           llm_eval.py: LLM output-quality + agent evals with gates
postgres (pgvector, Alembic migrations head 0016_open_to_recruiters)
```

### Model suite (real trained artifacts)

| Model | What it is | Trained by |
|---|---|---|
| M0 embedder | MiniLM-384 sentence embeddings (backbone) | off-the-shelf, live |
| M1 `skill2vec` | skill representation + transfer classification | `ml/train_skill2vec.py` |
| M2 `ranklet` | LightGBM learning-to-rank re-ranker | `ml/train_ranklet.py` |
| M3 `calibrator` | isotonic readiness calibration (score → probability) | `ml/calibrate.py` |
| M4 `preference-learner` | per-user utility weights + skill affinities (online, bounded) | feedback API |
| M5 `elicitation` | Bradley-Terry cold-start weight fit from 3 pairwise choices | `/onboarding` |

Artifacts and metrics are recorded in the `model_registry` table (visible in the admin console).

### Deterministic scoring (plan §6/§7)

```
FinalScore = 0.30·Semantic + 0.25·Skill + 0.15·Experience + 0.10·Education
           + 0.05·(Project + Location + Salary + CareerInterest)      Σw = 1.00
```

- All components ∈ [0, 100]; every input comes from DB/config — no network calls inside scoring.
- Weights are configurable with precedence **code defaults → `system_config` (admin PUT /admin/config, validated Σ=1) → env**; M4 personalization adapts per-user within [0.5×, 2.0×] of that baseline.
- The LLM never touches a score, a ranking, or a query — validators enforce evidence spans, skill tokens, digit preservation, and strict filter whitelists.

### AI features

| Feature | Where | What the LLM does / doesn't do |
|---|---|---|
| **Career Copilot** (`/copilot`) | WhatsApp-style chat | Picks tools (search, recommendations, explain match, career-direction gap, tracker, resume tailoring, save job, update direction, remember/forget) and phrases answers. Every job, score and skill comes from a tool result; write tools only on explicit request. Long-term memory (preferences, dealbreakers, goals) is extracted after each turn, de-duplicated by embedding similarity, recalled per turn by cosine similarity, and user-editable. A daily "morning drop" posts top matches into the chat. Without a provider, a deterministic intent router drives the same tools. |
| **Skill disparity everywhere** | search, AI search, feed, job detail | Per-card matched / missing / transferable chips **plus** a result-set banner ("Docker is required by 12 of these 20 results and missing from your profile"). Pure counting over engine output — no LLM. |
| **Career direction (switchers)** | profile → Career Direction | Target roles/domains are used in retrieval (own FTS leg), the profile embedding, the live Adzuna query, and the interest component (title / skill / domain match — never a penalty). A direction-gap report shows, per target role, which skills real postings demand and whether you have, can transfer, or lack them (readiness %). |
| **AI shortlist** (`/hiring/shortlist`) | employer portal | Structures the JD (P2; skills must appear in the JD text, unioned with a rule-based scan), retrieves opted-in seekers + applicants, ranks with the deterministic engine, and writes "why they fit / what's missing" notes that are discarded unless every number survives. Non-applicants are anonymised. |
| **NL search, resume parse/autofill, explanation polish, resume optimizer** | existing | Structured outputs with validators (spans, skill tokens, digit preservation, field whitelists). |

## Prerequisites

- Docker Desktop
- Docker Compose v2

## Run the stack

```bash
copy .env.example .env
docker compose up --build
```

- App: http://localhost:3000
- API docs: http://localhost:8000/docs
- Readiness (DB probe): http://localhost:8000/api/v1/health/ready

Postgres is published on host port **55432** because 5432/5433 were already in use on this machine. The API still talks to Postgres on the Docker network at `postgres:5432`.

LLM routes degrade gracefully without keys; set `OPENAI_API_KEY` (or `LLM_PROVIDER=gemini` + `GEMINI_API_KEY`) in `.env` to enable them. **Kill switch:** `LLM_ENABLED=false` forces every route to its deterministic fallback. **Trial mode:** `LLM_STRICT=true` inverts the graceful degradation — any AI failure (missing provider, API error, validator rejection) returns HTTP 502 with the reason instead of silently falling back, so you can see exactly which features work with the LLM; set it back to `false` afterwards. Optional **explanation polish**: `EXPLAIN_LLM=true` lets the LLM rewrite explanation wording only — a digit-multiset validator discards the polish unless every number survives verbatim (default off).

**Live jobs, no pre-loaded corpus:** jobs come from two places only — employers post their own listings on the platform (native, applied to in-app), and every keyword search hits **Adzuna at query time** (also merged into unfiltered browse and the recommendation feed). A country or city typed into the search box is detected deterministically and ranked first (structured `location` stays a hard filter), and the live leg routes to that country's Adzuna marketplace. Nothing is ingested into the database: live results are transient, carry `source: "adzuna"` and `adzuna-<id>` ids, and their cards link out to the provider (Adzuna exposes no details API). The offline pipeline remains available for manual/corpus runs: `docker exec jobrec-backend python scripts/fetch_jobs_live.py --query "..."`; one-time removal of the legacy ingested corpus: `docker exec jobrec-backend python scripts/purge_ingest.py`.

**Job alerts:** saved jobs can seed daily or weekly similarity digests. The alert worker compares newly published jobs with each seeker's saved jobs, creates deduplicated in-app notifications, and can send optional email digests through SMTP. Configure alert preferences under Settings; use `/notifications` for the in-app inbox. Run a one-off match scan with `docker compose exec backend python scripts/send_job_alerts.py --dry-run`; the Compose `alerts` service runs the scheduled worker.

**Resume auto-fill:** on the Profile page, *Auto-fill from resume* uploads (or reuses) the stored resume, waits for the AI parse, and fills the editable form — name, headline, summary, location, phone, years, skills, education, work history. Nothing lands in the profile until you review the fields and press **Save Profile**; every field stays manually editable. Identity values the parser cannot verify against the resume text are dropped rather than trusted, and failed or pre-identity parses can be re-run from the stored file with the **Re-parse** button (`POST /profiles/me/resume/reparse`).

## Layout

- `frontend/` — Next.js App Router (30 pages: feed, gap, roadmap, simulator, careers, NL search, tracker, saved, settings, resume optimizer, onboarding, notifications, admin, employer hiring suite, …)
- `backend/` — FastAPI + Alembic; `app/api` (87 paths), `app/services`, `ml/` training + evaluation
- `backend/scripts/` — offline corpus pipeline (Adzuna/Arbeitnow/Remotive/Jobicy → preprocess → embed), one-time `purge_ingest.py`, and the historical synthetic interaction bootstrap
- `docker-compose.yml` — Postgres, API, frontend

## Testing

```bash
# backend (~250 tests: golden pins, API contracts, eval gates, ops)
cd backend
$env:DATABASE_URL='postgresql+psycopg://jobrec:change_me_in_production@localhost:55432/jobrec'
$env:EMBEDDING_BACKEND='minilm'
python -m pytest tests/ -q

# frontend
cd frontend
npx tsc --noEmit
npx next build
```

## Evaluation (plan §21/§24 — nothing ships on vibes)

```bash
cd backend

# train all models (M1 → datasets → M2 → M3), registers artifacts + metrics
python ml/train.py

# run the §21 evaluation harness: Precision@K / Recall@K / NDCG@K / MRR,
# baselines (popularity, deterministic, M2), F1 for skill extraction,
# ECE/Brier for calibration — report written to ml/reports/eval-YYYY-MM-DD.json
python ml/evaluate.py

# CI mode: exit 1 when a required gate fails
python ml/evaluate.py --gate
```

### LLM evals (`ml/llm_eval.py`)

```bash
python ml/llm_eval.py                 # all suites with the configured provider
python ml/llm_eval.py --suite agent --judge   # + LLM-as-judge helpfulness
python ml/llm_eval.py --provider fake # offline plumbing check (used in CI tests)
python ml/llm_eval.py --gate          # exit 1 when a gate fails
```

| Suite | Golden set | Metrics (gate) |
|---|---|---|
| `nl_search` | 20 labelled NL queries | field accuracy (≥ 0.80), skills F1, whitelist violations (= 0), spurious-field rate |
| `resume` | 50 golden resumes | skill P/R/F1 (F1 ≥ 0.85), hallucination rate (≤ 0.05), evidence-span validity |
| `explain` | generated contracts | digit faithfulness before the validator (≥ 0.90), hype-word rate, length |
| `agent` | 14 Copilot scenarios, canned tool results | tool-selection accuracy (≥ 0.80), argument accuracy, write safety (= 1.0), groundedness — every quoted % exists in tool output, no jobs invented for empty results (≥ 0.95) |

Ranking gates (enforced in `tests/test_eval_regression.py`): ranker NDCG@10 ≥ deterministic baseline, skill-extraction F1 ≥ 0.85, transfer F1 ≥ 0.80, calibration ECE ≤ 0.10, semantic Recall@20 ≥ 0.75, plus determinism property tests (same input → identical score, Σw = 1, simulator idempotence).

## Admin console (`/admin`, admin role)

- **Skill merge** — transactional re-point of profiles, jobs, aliases, edges, affinities, learning resources; target embedding refreshed; JSONB snapshots stay immutable.
- **Duplicate jobs** — embedding cosine ≥ 0.92 + title similarity ≥ 0.60 (detection only; merging is a human decision).
- **LLM usage** — per-route/per-model call counts, tokens, latency, flagged calls, estimated cost; kill-switch state.
- **Parse failures** — failed/stuck resume parses with retry.
- **Scoring weights** — edit + save with Σ = 1 validation; LLM/M4 hyperparameters surfaced.
- **Model registry** — trained artifacts, versions, metrics, on-disk check.
