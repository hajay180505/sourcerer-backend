# Beta deployment — Azure for Students (₹0 / $0)

Portal-only, **single origin**: `caddy → frontend + gateway → portal →
postgres` on one Ubuntu VM. No credit card anywhere in this flow. Used for
staging (and as the simple all-in-one option).

> **Production** runs split-origin instead — Cloudflare Pages for the frontend,
> this VM for the API only, Neon for Postgres. See **[PRODUCTION.md](PRODUCTION.md)**.
> Steps 1–3 below (Azure account, VM, swap/Docker) apply to both.

## 1. Azure for Students account (no card)

1. Go to <https://azure.microsoft.com/free/students/> → **Start free**.
2. Sign in with a Microsoft account, verify with your **university email**
   (`...@psgtech.ac.in`) when asked for academic verification.
3. You get **$100 credit (12 months)** plus always-free services, including
   **750 h/month of a B1s VM** — a VM running 24/7 is fully covered.
   Credit renews yearly while you can re-verify as a student.

## 2. Create the VM

Portal → *Virtual machines* → *Create*:

- **Image**: Ubuntu Server 24.04 LTS (x64)
- **Size**:
  - `B1s` (1 vCPU / 1 GiB) — **always free**. Works with the swapfile below;
    office→PDF conversions are slow the first time each file is opened
    (cached forever after).
  - `B2s` (2 vCPU / 4 GiB) — smoother; ~$30–35/mo **from the $100 credit**,
    still ₹0 out of pocket for roughly a semester of beta.
    You can start on B1s and resize later in one click.
- **Authentication**: SSH public key (download the .pem).
- **Inbound ports**: allow **22, 80, 443**.

After creation, open the VM's **Public IP resource → Configuration**:

- Set assignment to **Static**.
- Set a **DNS name label**, e.g. `sourcerer-beta` →
  gives you `sourcerer-beta.<region>.cloudapp.azure.com`. That's your
  `SITE_ADDRESS` — a real DNS name, so Let's Encrypt and Google OAuth work
  without buying a domain.

## 3. Prepare the VM

```bash
ssh -i key.pem azureuser@<public-ip>

# Swap (required on B1s, harmless on bigger sizes)
sudo fallocate -l 3G /swapfile && sudo chmod 600 /swapfile
sudo mkswap /swapfile && sudo swapon /swapfile
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab

# Docker
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker $USER && exit   # re-ssh after this
```

## 4. Deploy

```bash
git clone https://github.com/<you>/sourcerer.git && cd sourcerer

# Service-account key (copy from your machine):
#   scp -i key.pem secrets/acc.json azureuser@<ip>:~/sourcerer/secrets/acc.json
mkdir -p secrets   # then place acc.json inside

cd deploy
cp .env.beta.example .env
nano .env          # SITE_ADDRESS, secrets, admin emails — see comments inside

docker compose -f docker-compose.beta.yml up -d --build
```

First build takes a while on B1s (LibreOffice layer + Next.js build; the swap
carries it). Then:

```bash
curl -s https://<SITE_ADDRESS>/health
docker compose -f docker-compose.beta.yml logs -f portal   # watch first catalog sync
```

## 5. Google OAuth client — add production URLs

In Google Cloud Console → your OAuth client, **add** (keep the localhost
entries for dev):

- Authorized JavaScript origin: `https://<SITE_ADDRESS>`
- Authorized redirect URI: `https://<SITE_ADDRESS>/api/v1/portal/auth/callback`

If the OAuth consent screen is still in *Testing* mode, either add beta
users' emails as test users or publish the app — otherwise only listed
accounts can sign in.

## 6. Smoke test

1. `https://<SITE_ADDRESS>/resources` → sign in with a non-admin Google
   account → browse index only, request access to a folder.
2. Sign in as an admin → `/admin` → approve → non-admin can open files.
3. Non-admins see only Resources in the nav; `/` redirects them to
   `/resources`; chat/quiz/ingest APIs return 404 at the gateway.

## Operations

| Task | Command (in `deploy/`) |
|---|---|
| Update to latest code | `git pull && docker compose -f docker-compose.beta.yml up -d --build` |
| Logs | `docker compose -f docker-compose.beta.yml logs -f portal` |
| Manual catalog sync | Admin UI → *Sync now* |
| DB backup | `docker compose -f docker-compose.beta.yml exec postgres pg_dump -U sourcerer sourcerer_portal > backup.sql` |

Notes:
- Postgres is not published to the internet (compose-internal only).
- The PDF-conversion cache lives in the `portal-cache` volume and survives
  restarts; safe to delete anytime (rebuilds on demand).
- Certificates auto-renew (Caddy).
- The portal container runs as a non-root user. The mounted service-account
  key must be world-readable: `chmod 644 secrets/acc.json`. Keep only the key
  you actually use in `secrets/` — the whole directory is mounted read-only.
- The `DB backup` above is manual. Schedule it (e.g. a daily cron running the
  `pg_dump` line to off-box storage) — all users, grants, and the audit trail
  live only in the `pgdata` volume; losing the VM loses them.
