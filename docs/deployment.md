# Deployment

The whole stack runs with Docker Compose.

```bash
cp .env.schema .env       # fill in credentials
docker compose up -d --build
```

## Services & ports

| Compose service | Runtime image | Published |
|---|---|---|
| `frontend` | node:22-alpine (standalone Next.js) | **3000** |
| `gateway` | python:3.13-slim | **8001** |
| `ingestion` / `ingestion-worker` | python:3.13-slim (+docling/torch-cpu) — one shared image | — |
| `retrieval` | python:3.13-slim | — |
| `quiz` | python:3.13-slim (+transformers/torch-cpu) | — |
| `portal` | python:3.13-slim (+headless LibreOffice, ~1 GB) | — |
| `postgres` | postgres:17-alpine | 5433 (host convenience) |
| `redis` | redis:latest | 6380 (host convenience) |

Only the frontend and gateway are published; services talk over the compose network.

## Image strategy

Each service has its own **multi-stage** Dockerfile building from the repo root.

**Builder stage** (`ghcr.io/astral-sh/uv:python3.13-bookworm-slim`):

1. Copy workspace manifests (`pyproject.toml`, `uv.lock`, member pyprojects) — cached layer
2. `uv sync --frozen --no-dev --package <service> --no-install-workspace` — third-party deps only
3. Copy `libs/` + the service's source
4. Final `uv sync --frozen --no-dev --package <service>` installs `sourcerer-core`

**Runtime stage** (`python:3.13-slim-bookworm`, the same base the uv image derives from) copies only `/srv/.venv`, `/srv/libs` (`sourcerer-core` is installed editable from there), and the service source — uv and intermediate build layers never ship.

`ingestion-worker` has no `build:` of its own; it reuses the `sourcerer-ingestion` image and only overrides the command, so the heaviest image builds once.

Containers get **CPU torch wheels** (small); CUDA never enters an image. For GPU workers see `docker-compose.gpu.yml`.

!!! note "Build context hygiene"
    `.dockerignore` excludes `**/.cache/` and `**/.venv/` at any depth. Local dev
    runs drop multi-GB model caches inside `services/*/.cache`; if those leak into
    the build context, every build re-transfers gigabytes and bakes models into
    the images. A no-change `docker compose build` should take seconds — if it
    takes minutes, check `transferring context` in the build output.

## Volumes

| Mount | Services | Purpose |
|---|---|---|
| `./secrets → /srv/secrets` (ro) | ingestion(+worker), portal | Google service account |
| `./.cache → /srv/.cache` | ingestion, retrieval, quiz | Shared HF/spaCy/NLTK/fastembed model cache — warm restarts |
| `./data → …/ingestion/data` | ingestion(+worker) | Incremental-tracking SQLite DB |
| `pgdata` (named) | postgres | Portal database |
| `portal-cache` (named) | portal | Converted-PDF disk cache |

## Required environment (`.env`)

| Variable | Used by |
|---|---|
| `QDRANT_URL`, `QDRANT_API_KEY`, `QDRANT_COLLECTION_NAME` | retrieval, quiz, worker |
| `GROQ_API_KEY`, `GROQ_MODEL` | retrieval, worker (tagging) |
| `GEMINI_API_KEY` | retrieval, quiz, worker (embeddings) |
| `TAVILY_API_KEY` | retrieval (optional web search) |
| `HF_TOKEN` | quiz (optional, model downloads) |
| `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `GOOGLE_CALLBACK_URL` | portal (Google sign-in; the callback URL must be registered as an authorized redirect URI on the OAuth client — it points at the **gateway**, e.g. `http://localhost:8001/api/v1/portal/auth/callback`) |
| `PORTAL_SESSION_SECRET`, `ADMIN_EMAILS`, `PORTAL_ROOT_FOLDER_ID` | portal |
| `POSTGRES_PASSWORD` | postgres + portal (defaults to `sourcerer` for local dev) |

Redis, cache-path, and in-network `DATABASE_URL` variables are injected by compose; you don't set them for Docker runs.

!!! note "Portal image size"
    The portal image bundles headless LibreOffice for office→PDF conversion
    (writer/impress/calc, `--no-install-recommends`), adding roughly 700 MB.
    Everything else about its build follows the standard uv two-stage pattern.

## Production topology (portal beta)

The shipped beta runs the portal only, split across two hosts that share the
`ewhizard.tech` registrable domain (so they are **same-site** and the session
cookie stays `SameSite=Lax; Secure`):

```
Browser
  ├─▶ sourcerer.ewhizard.tech       Cloudflare Pages  (static Next.js export)
  └─▶ api.sourcerer.ewhizard.tech   Azure VM: Caddy ─▶ gateway ─▶ portal ─▶ Neon
```

| Piece | Where | Config |
|---|---|---|
| Frontend | Cloudflare Pages project `sourcerer` (direct upload of `frontend/out/`) | `NEXT_PUBLIC_API_URL` baked at build time |
| API | Azure B1s VM | `deploy/docker-compose.prod.yml` + `deploy/Caddyfile.api` |
| Postgres | Neon (managed) | `DATABASE_URL` in `deploy/.env` |

`next.config.ts` defaults to `output: "export"` — the app is a pure client SPA,
so every route prerenders to static HTML and the viewer takes its file id from
`?fileId=` rather than a dynamic path segment. The Docker image sets
`NEXT_OUTPUT=standalone` to build the Node-server flavour for the single-origin
compose stack instead.

Managed-Postgres URLs carry libpq-only params (`sslmode`, `channel_binding`)
that asyncpg rejects; `app/db/session.py` translates `sslmode` into asyncpg's
`ssl` connect arg and drops the rest, and Alembic reuses that same
normalization so startup migrations reach Neon too.

The full bring-up runbook is `deploy/PRODUCTION.md`.
