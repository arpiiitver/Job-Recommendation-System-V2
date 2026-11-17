# Deploying JobRec (free tier)

| Part | Host | Free tier notes |
|---|---|---|
| Frontend (Next.js) | Vercel (Hobby) | always on |
| Backend (FastAPI + MiniLM via ONNX + agent) | Render free web service, from a Docker Hub image | 512 MB RAM / 0.1 CPU; sleeps after 15 min idle, first request then takes ~1 min |
| Database (Postgres 18 + pgvector) | Neon free | 0.5 GB; compute auto-suspends when idle, wakes in ~1 s |
| Container registry | Docker Hub (1 free private repo) | keeps the backend image private |

Nothing goes to GitHub: the image is pushed to a private Docker Hub repo that Render
pulls, and the frontend is uploaded straight to Vercel by its CLI.

**Why it fits in 512 MB:** the production image (`deploy/backend/`) runs the same
all-MiniLM-L6-v2 embedding model through onnxruntime (`fastembed`) instead of PyTorch —
identical vectors (cosine 1.0) at ~1/10 the memory. Measured under a hard 512 MB limit
with no swap: 96 MB idle, 372 MB peak across recommendations, live search, NL search,
the Copilot and the employer shortlist; no OOM.

## 1. Database — Neon

1. Create a project at <https://console.neon.tech>, region **AWS US East 1** (next to Render's US region).
2. *Connect* → turn **Connection pooling OFF** → copy the string.
3. In `deploy/space-secrets.env` set
   `DATABASE_URL=postgresql+psycopg://USER:PASS@HOST/neondb?sslmode=require&channel_binding=require`
   (note `+psycopg`).

Tables, the `vector` extension and the skill taxonomy are created on every boot
(`start.sh`: `alembic upgrade head` → `seed_canonical`, both idempotent).

## 2. Backend image — Docker Hub

```bash
docker login                                    # Docker Hub account
bash deploy/backend/stage.sh /tmp/jobrec-api    # clean build context (no secrets/caches)
docker build -t <dockerhub-user>/jobrec-api:latest /tmp/jobrec-api
docker push <dockerhub-user>/jobrec-api:latest
```

Make the repository **private** on hub.docker.com, and create a read-only
**access token** (Account settings → Personal access tokens) for Render.

## 3. Backend service — Render

1. <https://dashboard.render.com> → *New* → *Web Service* → **Existing image**.
2. Image URL `docker.io/<dockerhub-user>/jobrec-api:latest`; add a registry credential
   (Docker Hub username + the access token).
3. Region **Virginia (US East)**, instance type **Free**.
4. *Environment*: add every line of `deploy/space-secrets.env`
   (`CORS_ORIGINS` / `FRONTEND_PUBLIC_URL` after step 4).
5. *Settings* → Health check path `/api/v1/health`.

API URL: `https://<service-name>.onrender.com` (docs at `/docs`).

## 4. Frontend — Vercel

```bash
cd frontend
npx vercel login
npx vercel deploy --prod \
  --build-env NEXT_PUBLIC_API_URL=https://<service-name>.onrender.com \
  --build-env API_INTERNAL_URL=https://<service-name>.onrender.com \
  --env NEXT_PUBLIC_API_URL=https://<service-name>.onrender.com \
  --env API_INTERNAL_URL=https://<service-name>.onrender.com
```

Then set `CORS_ORIGINS` and `FRONTEND_PUBLIC_URL` on Render to the Vercel URL and let it redeploy.

## Updating

- Backend: re-run step 2 (`stage` → `build` → `push`), then Render → *Manual Deploy* → *Deploy latest reference*.
- Frontend: `npx vercel deploy --prod` from `frontend/`.

## Free-tier limits to know before a demo

- **Cold start**: the API sleeps after 15 min without traffic — open the site ~1 min before showing it.
- **Uploaded resume files** live on the container's ephemeral disk and vanish on restart/redeploy;
  the parsed profile data lives in Neon and survives.
- **Gemini free tier** rate limits: each user is capped at `RATE_LIMIT_LLM_PER_MINUTE` (20) AI
  requests/min, and provider errors degrade to rule-based answers instead of failing.
- **Saved-job alert worker** is off unless `ALERTS_ENABLED=true` (it then runs inside the API container).
