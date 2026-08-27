# Environments & Git flow

## Branch flow

Code is promoted **upward**; each long-lived branch maps to a deploy tier.

```
feature/*  ┐
fix/*      ├─▶  dev  ──▶  staging  ──▶  main
hotfix/*   ┘   (integration)  (pre-prod)  (production)
```

- Branch features off `dev`; open a PR into `dev`.
- Promote `dev → staging` (deploy + smoke-test), then `staging → main` (tag +
  deploy prod). Never push directly to `main`/`staging`.
- Hotfixes branch off `main`, then back-merge into `staging` and `dev`.

CI (`.github/workflows/ci.yml`) runs the portal test suite and the frontend
type-check/build on every PR and push to these branches. Full rules,
hotfix procedure, and the release checklist are in `CONTRIBUTING.md` at the
repo root.

## Deploy tiers

| Tier | Branch | Compose file | Env source | Cookies | Origin |
|---|---|---|---|---|---|
| **dev** | `dev` | `docker-compose.yml` (root) | `.env` from `.env.schema` | insecure, `Lax` | `localhost:3000/3001` |
| **staging** | `staging` | `deploy/docker-compose.beta.yml` | `deploy/.env` from `.env.staging.example` | `Secure`, `Lax` | `https://<staging host>` |
| **prod** | `main` | `deploy/docker-compose.prod.yml` + Cloudflare Pages | `deploy/.env` from `.env.prod.example` | `Secure`, `Lax` | `https://sourcerer.ewhizard.tech` + `https://api.sourcerer.ewhizard.tech` |

Dev and staging run the **single-origin** stack (Caddy auto-HTTPS → frontend +
gateway → portal → postgres in a container). Prod is **split-origin**: a static
Next.js export on Cloudflare Pages, an API-only VM, and managed Postgres (Neon)
— the two hosts are same-site subdomains, so cookies stay `SameSite=Lax`. Keep
each tier's secrets, database, and ideally its OAuth client **distinct**. See
`deploy/PRODUCTION.md`, `deploy/ENVIRONMENTS.md`, and
[Deployment](deployment.md) for runbooks, backups, and TLS.

## Continuous deployment

- **Frontend** — Cloudflare Pages' Git integration builds `frontend/` (output
  `out/`): Production on `main`, Preview for every other branch and PR.
- **Backend** — `.github/workflows/deploy-portal.yml` waits for a **green CI run
  on `main`**, then SSHes to the VM, pulls, rebuilds the prod compose stack, and
  polls `/health`. Manual `workflow_dispatch` redeploys are allowed. It needs
  the `SSH_HOST`, `SSH_USER`, `SSH_KEY` (optional `SSH_PORT`) Actions secrets.
