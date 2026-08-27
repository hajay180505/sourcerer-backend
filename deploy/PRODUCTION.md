# Production deployment — split origin ($0, no credit card)

The beta ships **only the portal**. The frontend is a static export on
**Cloudflare Pages**; the API runs on an **Azure for Students VM**; Postgres is
**managed (Neon)** and lives off the VM.

```
Browser
  ├─▶ sourcerer.ewhizard.tech       Cloudflare Pages  (static Next.js export)
  └─▶ api.sourcerer.ewhizard.tech   Azure VM: Caddy TLS ─▶ gateway ─▶ portal ─▶ Neon
```

Both hosts are subdomains of `ewhizard.tech`, i.e. **same-site**. That keeps the
session cookie on `SameSite=Lax; Secure` (no third-party-cookie fragility), lets
`<video>` load bytes with cookies attached (`Sec-Fetch-Site: same-site` satisfies
the portal's content guard), and leaves only a plain CORS allowance to configure.

> The single-origin flavour (app + API behind one hostname on one VM) still
> exists as `docker-compose.beta.yml` — see [README.md](README.md). Use it for
> staging or if you don't want the Pages split.

## 1. Neon — managed Postgres

1. <https://neon.tech> → sign up (GitHub/Google, no card) → create a project in
   the region nearest the VM → create database `sourcerer_portal`.
2. Copy the **pooled** connection string. Rewrite the scheme for SQLAlchemy and
   keep `?sslmode=require`:

   ```
   postgresql+asyncpg://user:pw@ep-xxx-pooler.region.aws.neon.tech/sourcerer_portal?sslmode=require
   ```

   `app/db/session.py` turns `sslmode` into asyncpg's `ssl` arg and strips the
   libpq-only params asyncpg would reject; the same normalization runs for
   `alembic upgrade head` at container start.

Neon scales to zero when idle and resumes in ~0.5 s — `pool_pre_ping` on the
engine drops connections killed during a suspend.

## 2. Azure VM

Create the VM exactly as in [README.md §1–§3](README.md) (Ubuntu 24.04, **B1s**,
ports 22/80/443, static public IP, 3 GB swap, Docker). No DNS label needed — the
hostname comes from your own domain.

## 3. DNS (Cloudflare, zone `ewhizard.tech`)

| Record | Type | Value | Proxy |
|---|---|---|---|
| `api.sourcerer` | A | VM public IP | **DNS only (grey cloud)** |
| `sourcerer` | CNAME | *(added automatically by Pages in step 6)* | proxied is fine |

The API record **must** be grey-cloud: a proxied record breaks Caddy's ACME
challenge and buffers video/Range responses.

## 4. Deploy the API

```bash
ssh -i key.pem azureuser@<public-ip>
git clone https://github.com/EWhizardTech/sourcerer.git && cd sourcerer

mkdir -p secrets   # then: scp secrets/acc.json onto the VM, chmod 644

cd deploy
cp .env.prod.example .env
nano .env      # API_ADDRESS, APP_ORIGIN, DATABASE_URL (Neon), secrets

docker compose -f docker-compose.prod.yml up -d --build
curl -s https://api.sourcerer.ewhizard.tech/health
```

The first build is slow on B1s (LibreOffice layer); the swap carries it. No
frontend or Postgres container is built here.

## 5. Google OAuth client

In Google Cloud Console → your OAuth client, **add** (keeping the dev entries):

- Authorized JavaScript origin: `https://sourcerer.ewhizard.tech`
- Authorized redirect URI:
  `https://api.sourcerer.ewhizard.tech/api/v1/portal/auth/callback`

## 6. Cloudflare Pages — frontend (direct upload)

The project already exists — created with the CLI, so it is a **direct-upload**
project (not Git-connected; Cloudflare cannot convert between the two):

```bash
npx wrangler login
npx wrangler pages project create sourcerer --production-branch=main
```

Live at <https://sourcerer-4gj.pages.dev>. To publish by hand:

```bash
cd frontend
NEXT_PUBLIC_API_URL=https://api.sourcerer.ewhizard.tech npm run build
npx wrangler pages deploy out --project-name=sourcerer --branch=main
```

`--branch=main` matches the project's production branch, so that upload becomes
the production deployment; any other `--branch` value creates a preview.
`NEXT_PUBLIC_API_URL` is baked into the bundle at build time — change the API
hostname and you must rebuild, not just redeploy.

Custom domain: dashboard → Workers & Pages → `sourcerer` → *Custom domains* →
add `sourcerer.ewhizard.tech`. Wrangler has no `pages domain` command.

`next.config.ts` emits the static export (`out/`) by default; the Docker image
sets `NEXT_OUTPUT=standalone` for the single-origin compose flavour.

## 7. CD — both halves

| Workflow | Trigger | Does |
|---|---|---|
| `.github/workflows/deploy-portal.yml` | green CI on `main`, or manual | SSH to the VM → `git pull --ff-only` → `docker compose -f deploy/docker-compose.prod.yml up -d --build` → poll `/health` |
| `.github/workflows/deploy-frontend.yml` | green CI on `main`, or manual | `npm ci && npm run build` (static export) → `wrangler pages deploy out` |

Repo → *Settings* → *Secrets and variables* → *Actions*:

| Secret | Value |
|---|---|
| `SSH_HOST` | VM public IP or `api.sourcerer.ewhizard.tech` |
| `SSH_USER` | `azureuser` |
| `SSH_KEY` | private key (PEM) of a key authorized on the VM |
| `SSH_PORT` | optional, defaults to 22 |
| `CLOUDFLARE_API_TOKEN` | API token with *Account → Cloudflare Pages → Edit* |
| `CLOUDFLARE_ACCOUNT_ID` | shown by `npx wrangler whoami` |

Optional repository **variable** `NEXT_PUBLIC_API_URL` overrides the API
hostname baked into the frontend build (default:
`https://api.sourcerer.ewhizard.tech`).

Until those secrets exist the workflows fail at their deploy step; publish by
hand with the step-4 and step-6 commands in the meantime.

Both workflows live on `dev` right now — they only start firing once `main`
carries them and CI passes there.

## 8. Verify

- `GET https://api.sourcerer.ewhizard.tech/health` → `{"status":"ok"}` (Neon
  reachable, migrations applied).
- Sign in at `https://sourcerer.ewhizard.tech` → lands on `/home`; DevTools →
  Application → Cookies shows `sourcerer_session` on host
  `api.sourcerer.ewhizard.tech`, `Secure`, `HttpOnly`, `SameSite=Lax`.
- `GET /api/v1/portal/auth/me` returns 200 from the app (CORS + cookie OK).
- Open a PDF; **play a video and seek** → 206 responses, no 403 (the content
  guard's same-site media branch).
- Deep-link `https://sourcerer.ewhizard.tech/resources/view?fileId=<id>` loads
  directly (static-export routing).
- Merge to `main` → Pages Production build **and** the VM redeploy both fire.

## Operations

| Task | Command (in `deploy/` on the VM) |
|---|---|
| Update to latest code | `git pull && docker compose -f docker-compose.prod.yml up -d --build` |
| Logs | `docker compose -f docker-compose.prod.yml logs -f portal` |
| Manual catalog sync | Admin UI → *Sync now* |
| DB backup | Neon dashboard (point-in-time restore) or `pg_dump "<DATABASE_URL>"` |

Notes:
- Users, grants, and the audit trail now live in **Neon**, not on the VM — the
  VM is disposable; only `deploy/.env` and `secrets/acc.json` are precious.
- The PDF-conversion cache (`portal-cache` volume) is rebuildable; safe to drop.
- Certificates auto-renew (Caddy). The portal container runs non-root, so
  `secrets/acc.json` must be `chmod 644`.
- Neon's free tier is 0.5 GB — plenty for catalog metadata (~5.5k nodes), but
  watch it as the library grows.
