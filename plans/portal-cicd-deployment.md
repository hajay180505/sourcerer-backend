# Sourcerer Portal — CI/CD & Deployment Plan (Beta)

## Context

The beta ships **only the portal**: the FastAPI backend (`services/portal` +
`gateway`) and the Next.js app (`frontend/`). RAG services stay disabled. Goal: a
**$0, no-credit-card** deployment wired into the `dev → staging → main` git flow.
CI already runs (`.github/workflows/ci.yml`: portal `pytest` + frontend
`tsc`/`build`); this plan adds **CD** and picks hosts.

**Decisions locked in with the owner:**
- Frontend → **Cloudflare Pages** (not Vercel).
- Prod Postgres → **managed** (Neon recommended; Supabase possible) — off the VM.
- Domain owned: **`ewhizard.tech`** → use `sourcerer.ewhizard.tech` (app) +
  `api.sourcerer.ewhizard.tech` (backend). Same registrable domain ⇒ **same-site**,
  which removes every cross-origin cookie/video gotcha.
- Backend → **Azure for Students VM** (no card, verified by university email).

---

## Target architecture

```
Browser
  ├─▶ sourcerer.ewhizard.tech        Cloudflare Pages  (static Next.js export, unlimited bw)
  └─▶ api.sourcerer.ewhizard.tech    Azure VM: Caddy TLS ─▶ gateway ─▶ portal ─▶ Neon (managed PG)
```

- **Same-site subdomains** → session cookie stays `SameSite=Lax; Secure`; no
  third-party-cookie fragility; the content guard's media branch passes. Only a
  normal **CORS** allowance (cross-origin, same-site) is needed.
- **Cloudflare Pages static** → unlimited bandwidth, no Workers runtime, no card.
- **Neon** → managed Postgres off the VM (auto-resume, durable), no card.
- **Azure VM** now only runs Caddy + gateway + portal → lighter (more headroom
  for LibreOffice).

Everything recurring is **$0** and needs **no credit card**; the domain is
already owned.

---

## 1. Hosting choices

### Frontend — Cloudflare Pages via **static export** (recommended)

The app is a pure client SPA (every page `"use client"`, all data fetched
client-side against the API — no server components, middleware, or Next API
routes). So it can be **statically exported** and served by Pages as plain files:
unlimited bandwidth, simplest hosting, no Workers.

One blocker: the single dynamic route `/resources/view/[fileId]` can't be
statically prerendered (unknown ids). **Fix: make it a query-param page** (see
§3.1). After that, `next build` with `output: "export"` emits `frontend/out/`,
which Pages serves directly.

> **No-code-change alternative:** the **OpenNext Cloudflare adapter**
> (`@opennextjs/cloudflare`) runs Next SSR on Cloudflare Workers — keeps the
> dynamic route untouched, but adds build config + a Workers runtime (free
> 100k req/day). Prefer static export; it fits this SPA better.

### Prod Postgres — **Neon** (recommended)

| | Neon | Supabase |
|---|---|---|
| Card | No | No |
| Idle behavior | **Scale-to-zero, auto-resume ~0.5 s** | **Pauses after 7 days idle → manual unpause** |
| Free storage | 0.5 GB | 0.5 GB |
| Fit for low-traffic beta | ✓ (self-heals) | ✗ footgun (silent pause) |

**Neon** for a beta that may sit idle for days. Supabase works but its 7-day
pause needs manual dashboard intervention. Both speak standard Postgres → asyncpg
connects with **SSL** (small config change, §3.3). Neon also gives durability/
backups the on-VM `pgdata` volume didn't.

### Backend — Azure for Students VM

Only realistic **no-card** host for the ~1 GB LibreOffice image + long-running
server (Koyeb 512 MB too small; Render/Oracle/GCP/Fly want a card). **B1s**
(1 vCPU/1 GB) + 3 GB swap; now runs only Caddy + gateway + portal (Postgres is on
Neon).

---

## 2. Required app changes

### 3.1 Frontend: static export + query-param viewer route
- `frontend/next.config.ts`: `output: "export"` (was `"standalone"`);
  `images: { unoptimized: true }`; consider `trailingSlash: true` for clean Pages
  routing.
- Convert `frontend/app/resources/view/[fileId]/page.tsx` →
  `frontend/app/resources/view/page.tsx` reading `useSearchParams().get("fileId")`.
- Update the three link builders: `openItem` in
  `frontend/components/portal/file-browser.tsx`
  (`/resources/view/${id}` → `/resources/view?fileId=${id}`), `shareUrl()` for
  files, and the `ShareMenu` url in the viewer page. (`/resources?folder=` is
  already query-based — no change.)
- `frontend/public/pdf.worker.min.mjs` stays a static asset (served by Pages).

### 3.2 Cross-origin config (same-site, so **Lax stays**)
Backend `deploy/.env` on the VM:
```
PORTAL_COOKIE_SECURE=true
PORTAL_COOKIE_SAMESITE=lax                       # same-site subdomains → Lax works
PORTAL_FRONTEND_ORIGIN=https://sourcerer.ewhizard.tech
PORTAL_ALLOWED_ORIGINS=https://sourcerer.ewhizard.tech
GOOGLE_CALLBACK_URL=https://api.sourcerer.ewhizard.tech/api/v1/portal/auth/callback
CORS_ORIGINS=https://sourcerer.ewhizard.tech     # gateway; allow_credentials already true
```
Cloudflare Pages build env: `NEXT_PUBLIC_API_URL=https://api.sourcerer.ewhizard.tech`.
Google OAuth console: add JS origin `https://sourcerer.ewhizard.tech` + redirect
URI `https://api.sourcerer.ewhizard.tech/api/v1/portal/auth/callback`.

Because the two hosts are same-site, the `sourcerer_session` cookie (host-only on
the api subdomain, `SameSite=Lax`) is sent on the app's credentialed XHR, the
`<video>` media guard passes (`Sec-Fetch-Site: same-site`), and there is **no
third-party-cookie problem** and **no code change to the content guard**.

### 3.3 Backend: managed-Postgres wiring
- `deploy/.env`: `DATABASE_URL=postgresql+asyncpg://<user>:<pass>@<ep>.neon.tech/<db>`.
- `services/portal/app/db/session.py`: pass SSL to asyncpg for Neon, e.g.
  `create_async_engine(url, connect_args={"ssl": True})` (asyncpg ignores libpq
  `sslmode`; it needs `ssl`). Verify migrations run against Neon on portal start.
- `deploy/docker-compose.beta.yml`: for **prod**, drop the `postgres` service and
  its `depends_on` (portal talks to Neon over the internet). Keep `postgres` in
  the root `docker-compose.yml` for local dev. (A small `docker-compose.prod.yml`
  override, or an env-driven compose, is cleanest.)

### 3.4 Caddy: API-only
`deploy/Caddyfile` now serves one site `api.sourcerer.ewhizard.tech` →
`reverse_proxy` `/api/*` + `/health` to the gateway (no frontend to serve). Keep
the security headers.

### 3.5 DNS
- `sourcerer.ewhizard.tech` → Cloudflare Pages custom domain (CNAME to `*.pages.dev`; proxied is fine — static).
- `api.sourcerer.ewhizard.tech` → **A record** to the Azure VM public IP, **DNS-only (grey cloud, not proxied)** so Caddy can complete its Let's Encrypt challenge and stream video/Range directly.

---

## 3. CI/CD pipelines

### CI (exists — keep)
`ci.yml`: `backend` (portal pytest) + `frontend` (tsc + build) on PRs/pushes to
`dev`/`staging`/`main`.

### CD — frontend (Cloudflare Pages Git integration = least setup)
- Connect the GitHub repo in Cloudflare Pages → **Root directory `frontend`**,
  build command `npm run build`, **output directory `out`**, production branch
  `main`.
- Pages auto-builds: **Production** on `main`, **Preview** for other branches/PRs.
- Set `NEXT_PUBLIC_API_URL` as a Pages build env var (Production + Preview).
- No workflow YAML. (Alternative: `cloudflare/wrangler-action` in GitHub Actions
  if you want CI-gated deploys.)

### CD — backend (Azure VM via GitHub Actions, push-to-deploy)
New `.github/workflows/deploy-portal.yml`:
- **Trigger:** `workflow_run` after CI succeeds on `main` (deploy only green commits).
- **Steps:** SSH to the VM (`appleboy/ssh-action`) →
  `cd ~/sourcerer && git pull --ff-only && docker compose -f deploy/docker-compose.beta.yml up -d --build && docker image prune -f`.
- **Secrets** (repo → Settings → Actions): `SSH_HOST`, `SSH_USER`, `SSH_KEY`
  *(needs a repo admin to add)*.
- **Alternative (no inbound SSH):** cron/systemd timer on the VM pulling + `up -d`.

### Secrets — where things live
- **GitHub Actions:** SSH deploy creds only.
- **Cloudflare Pages:** `NEXT_PUBLIC_API_URL` (public).
- **Neon:** connection string → lives in `deploy/.env` on the VM (`DATABASE_URL`).
- **Backend app secrets** (`GOOGLE_CLIENT_SECRET`, `PORTAL_SESSION_SECRET`,
  Neon URL, `secrets/acc.json`): only in `deploy/.env` + `secrets/` **on the VM**,
  never in the repo/CI. Compose `${VAR:?}` guards + the fail-closed config
  validator enforce presence.

---

## 4. Environments

| Tier | Branch | Frontend (Cloudflare Pages) | Backend | DB |
|---|---|---|---|---|
| dev | `dev` | Preview build | local `docker-compose.yml` | local Postgres container |
| staging | `staging` | Preview build | *(optional)* 2nd VM | Neon (separate branch/db) |
| prod | `main` | Production (`sourcerer.ewhizard.tech`) | Azure prod VM | Neon (prod db) |

**Lean $0 beta:** one prod VM + Cloudflare Pages Production + Pages Previews for
PRs + one Neon project. Neon's DB branching makes a staging DB trivial later.

---

## 5. Bring-up runbook (prod)

1. **Neon** — create a project + database; copy the pooled connection string.
2. **Azure for Students** — verify with the university email; create an Ubuntu
   **B1s** VM; open 80/443; note its public IP.
3. **DNS (Cloudflare)** — `api.sourcerer.ewhizard.tech` A → VM IP (grey cloud);
   `sourcerer.ewhizard.tech` reserved for the Pages custom domain.
4. **VM** — install Docker + compose; add 3 GB swap; clone the repo; copy
   `secrets/acc.json` (`chmod 644`); write `deploy/.env` from `.env.beta.example`
   with the §3.2 values + `DATABASE_URL` (Neon); `docker compose -f
   deploy/docker-compose.beta.yml up -d --build`; check
   `https://api.sourcerer.ewhizard.tech/health`.
5. **Google OAuth console** — add the app JS origin + the api callback URI.
6. **Cloudflare Pages** — connect repo (root `frontend`, output `out`), set
   `NEXT_PUBLIC_API_URL`, deploy; add `sourcerer.ewhizard.tech` as the custom
   domain.
7. **CD** — an admin adds the Actions SSH secrets + `deploy-portal.yml`. Push to
   `main` → backend redeploys; Pages auto-deploys the frontend.

## 6. Verification

- `GET https://api.sourcerer.ewhizard.tech/health` → `{status: ok}` (Neon reachable, migrations ran).
- Sign in at `https://sourcerer.ewhizard.tech` → lands on `/home`; DevTools shows the cookie `Secure`, `HttpOnly`, `SameSite=Lax`, host `api.sourcerer.ewhizard.tech`.
- `GET /api/v1/portal/auth/me` → 200 with credentials (CORS + same-site cookie OK).
- View a PDF; **play a video and seek** (206, no 403) — confirms the media guard passes cross-subdomain.
- Deep-link `https://sourcerer.ewhizard.tech/resources/view?fileId=<id>` loads directly (static export routing).
- Merge to `main` → Pages Production + Azure redeploy both fire.

## 7. Cost / no-card summary

| Item | Cost | Card |
|---|---|---|
| Cloudflare Pages (frontend, static, unlimited bw) | $0 | No |
| Neon (managed Postgres) | $0 | No |
| Azure for Students VM (Caddy + gateway + portal) | $0 (credit + always-free VM) | No |
| Domain (`ewhizard.tech`) | already owned | — |
| **Recurring total** | **$0** | **No** |

## 8. Flags / follow-ups

- **Code changes needed before deploy** (I can implement): static-export
  `next.config.ts`, the viewer route → query param + 3 link builders, asyncpg SSL
  in `session.py`, drop `postgres` from the prod compose, API-only `Caddyfile`.
- **Repo-admin actions** (blocking): add GitHub Actions deploy secrets, add the
  deploy workflow, set branch protection — the automation account lacks admin.
- **Neon free tier** is 0.5 GB + scale-to-zero; fine for the portal's small
  relational data (users/grants/catalog metadata). Watch storage as the catalog
  grows (~5.5k nodes is tiny).
- **Cloudflare Pages** has no non-commercial clause (unlike Vercel Hobby) — safe
  to monetize later.

---

### Sources
- [Cloudflare Pages — free static hosting, unlimited bandwidth](https://blog.vibecoder.me/vercel-vs-netlify-vs-cloudflare-pages)
- [Neon vs Supabase free tier 2026 (scale-to-zero vs 7-day pause)](https://agentdeals.dev/neon-vs-supabase)
- [Neon free tier — no credit card, scale-to-zero](https://tech-insider.org/neon-vs-supabase-2026/)
- [Azure for Students — no credit card, university email](https://azure.microsoft.com/en-us/free/students)
- [SameSite / cross-site cookies (Chromium)](https://www.chromium.org/updates/same-site/faq/)
