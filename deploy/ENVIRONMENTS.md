# Environments

Three tiers, mapped to the three long-lived branches (see `../CONTRIBUTING.md`).

| Tier | Branch | Compose file | Env source | Cookies | Origin |
|---|---|---|---|---|---|
| **dev** | `dev` | `docker-compose.yml` (root) | root `.env` (from `.env.schema`) | `Secure=false`, `SameSite=Lax` | `http://localhost:3000/3001` |
| **staging** | `staging` | `deploy/docker-compose.beta.yml` | `deploy/.env` (from `.env.staging.example`) | `Secure=true`, `SameSite=Lax` | `https://<staging host>` |
| **prod** | `main` | `deploy/docker-compose.prod.yml` + Cloudflare Pages | `deploy/.env` (from `.env.prod.example`) | `Secure=true`, `SameSite=Lax` | app `https://sourcerer.ewhizard.tech`, API `https://api.sourcerer.ewhizard.tech` |

Dev and staging run the **single-origin** stack (one Caddy serving frontend +
API, Postgres in a container). Prod is **split-origin**: the frontend is a
static export on Cloudflare Pages, the VM serves only the API, and Postgres is
managed (Neon) — see `PRODUCTION.md`. Keep each tier's secrets, database, and
ideally its OAuth client **distinct**.

## Fail-closed guards (don't fight them)

In staging/prod (`PORTAL_COOKIE_SECURE=true`) the app refuses to boot unless:

- `PORTAL_SESSION_SECRET` is a strong, non-default value (≥ 32 chars),
- `PORTAL_ROOT_FOLDER_ID` is set,
- `ADMIN_EMAILS` lists at least one admin.

And `docker compose` itself hard-fails on missing `deploy/.env` values:
`SITE_ADDRESS` + `POSTGRES_PASSWORD` (single-origin) or `API_ADDRESS`,
`APP_ORIGIN` + `DATABASE_URL` (prod), plus `PORTAL_SESSION_SECRET` and
`PORTAL_ROOT_FOLDER_ID` in both.

## Bring up a tier

```bash
# dev (local)
cp .env.schema .env      # fill in, then:
docker compose up -d --build

# staging (on the VM, in deploy/) — single origin
cp .env.staging.example .env   # fill in
docker compose -f docker-compose.beta.yml up -d --build

# prod (on the VM, in deploy/) — API only; frontend is on Cloudflare Pages
cp .env.prod.example .env      # fill in, incl. the Neon DATABASE_URL
docker compose -f docker-compose.prod.yml up -d --build
```

Operational notes (backups, key permissions, TLS) live in `README.md`; the
full production bring-up is in `PRODUCTION.md`.
