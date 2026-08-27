# Portal Service

Sourcerer as the face of the owner's Google Drive: visitors sign in with
Google, browse a **metadata-only index** of the resource library, request
timed access to folders and/or files, and — once the admin approves — view
content **in-app only**. Drive URLs are never exposed; all bytes flow through
the portal's read-only service account.

> **Scope guarantee — no ingestion, no cloud cost.** The portal never
> embeds, never writes to Qdrant, never calls Groq/Gemini, and never invokes
> the ingestion pipeline. Catalog sync stores names/ids/sizes only; file
> bytes move on demand when a granted user opens one specific file. The only
> external API is the free Google Drive API.

## Responsibilities

- **Auth** — Google OAuth code flow performed by the service itself
  (confidential client), issuing an `HttpOnly` HS256 JWT session cookie
  (`sourcerer_session`). Admins are the emails in `ADMIN_EMAILS`, evaluated
  per request. A CSRF origin guard rejects state-changing requests from
  origins outside `PORTAL_ALLOWED_ORIGINS`.
- **Catalog** — periodic + on-demand metadata walk of
  `PORTAL_ROOT_FOLDER_ID` (BFS, batched parent queries) into Postgres
  `drive_nodes`, with mark-and-sweep for deletions and materialized paths
  (`path_ids`) for access resolution. Dot-directories (`.obsidian`) and
  `PORTAL_SYNC_EXCLUDE` globs are pruned. Obsidian `[[wikilinks]]` are
  extracted incrementally (only new/changed `.md` files) into `md_links`
  for the graph view.
- **Visibility** — every node carries a tri-state `visibility`
  (`public` / `private` / NULL = inherit); the effective value is the nearest
  explicitly-set self-or-ancestor, defaulting to **private**. Inheritance is
  live: marking a folder public exposes its whole subtree (including files
  that sync in later) except explicitly-private descendants. Non-admin users
  only see effectively-public nodes, nodes their active grants cover (a
  grant overrides visibility), and the bare "structural" parent folders
  needed to reach them; only public nodes are requestable. Flipping a node
  private never touches existing grants or pending requests. Toggles live in
  the admin's Library view (`PATCH /admin/nodes/{id}/visibility`, audited as
  `visibility_changed`); the setting survives catalog syncs because the sync
  upsert never writes that column.
- **Access control** — users request access to (effectively-public)
  folders/files (`access_requests` + items); the admin approves with an
  adjustable period, producing one `grants` row per item. A folder grant
  covers its whole subtree via a single indexed `path_ids LIKE prefix`
  check. Grants can be extended, shortened, or revoked at any time; every
  decision and every content view is written to `audit_events`.
- **Content** — `/content/{id}/raw` streams bytes straight from the Drive
  API with `Range` passthrough (video seeking works through the gateway);
  `/content/{id}/pdf` serves office files (LibreOffice headless) and
  Google Docs/Slides/Sheets (Drive export) as PDFs from a
  content-addressed disk cache keyed by `md5Checksum`/`modifiedTime`.
  Access is re-checked live on every hit (revoke/expiry are immediate), and
  each byte-serving request must be an **in-app request carrying a valid,
  short-lived signed ticket** — see [Security](../security.md).

## Endpoints (all under `/api/v1/portal`)

| Group | Endpoints |
|---|---|
| auth | `GET /auth/login`, `GET /auth/callback`, `GET /auth/me`, `POST /auth/logout`, `GET /auth/verify-admin` |
| catalog | `GET /catalog/children`, `GET /catalog/search`, `GET /catalog/graph` |
| requests | `POST /requests`, `GET /requests/mine`, `POST /requests/{id}/cancel`, `GET /grants/mine` |
| content | `GET /content/{id}/meta` (mints a ticket), `GET /content/{id}/raw`, `GET /content/{id}/pdf` |
| admin | `GET/POST /admin/requests*`, `GET/PATCH/POST /admin/grants*`, `PATCH /admin/nodes/{id}/visibility`, `POST /admin/users/{id}/revoke-sessions`, `POST /admin/sync`, `GET /admin/sync/status`, `GET /admin/users`, `GET /admin/audit` |

## Storage

First relational store in the repo: **Postgres 17** (compose service
`postgres`, published on host port `5433`), SQLAlchemy 2 async + Alembic
(migrations run automatically at container start). Tables: `users`,
`drive_nodes`, `md_links`, `access_requests`, `access_request_items`,
`grants`, `audit_events`. `grants.node_id` deliberately has **no FK** to
`drive_nodes` so catalog sweeps never destroy grant history; a grant on a
vanished node simply matches nothing.

## Configuration

See the `# --- Resource portal ---` section of `.env.schema`:
`GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` / `GOOGLE_CALLBACK_URL` (the
OAuth client's authorized redirect URI **must** be the gateway URL, e.g.
`http://localhost:8001/api/v1/portal/auth/callback`),
`PORTAL_SESSION_SECRET`, `ADMIN_EMAILS`, `PORTAL_ROOT_FOLDER_ID`,
`PORTAL_FRONTEND_ORIGIN`, `PORTAL_ALLOWED_ORIGINS`, cookie flags, cache dir,
sync interval/excludes, conversion timeout, `POSTGRES_PASSWORD`,
`DATABASE_URL`, session TTL (`PORTAL_SESSION_TTL_SECONDS`, default 24 h), and
content-ticket lifetimes (`PORTAL_CONTENT_TICKET_TTL_SECONDS` = 5 min,
`PORTAL_CONTENT_STREAM_TTL_SECONDS` = 6 h).

In production (`PORTAL_COOKIE_SECURE=true`) the service **fails closed** at
startup unless `PORTAL_SESSION_SECRET` is strong (≥32 chars, non-default),
`PORTAL_ROOT_FOLDER_ID` is set, and `ADMIN_EMAILS` lists at least one admin.

## Frontend protection layer

This is the client-side **deterrence** tier; the enforceable controls (in-app
request guard, signed tickets, session revocation) live server-side and are
described in [Security](../security.md).

The UI wraps all viewers in `ProtectedContent` (deterrence, not DRM — OS
screenshots cannot be blocked by a browser): tiled per-viewer email
watermark (also baked into PDF canvas bitmaps), right-click/selection/drag
suppression, clipboard copy truncated to ~2 sentences with attribution,
print blanking, blur on tab/window blur, and a DevTools-open heuristic that
hides content. PDFs render canvas-only — no text layer to select or scrape.

## Image note

The portal image includes headless LibreOffice (writer/impress/calc,
`--no-install-recommends`) for office→PDF conversion — roughly 700 MB–1 GB.
Conversions are capped by a global semaphore (2) and a per-file lock, with a
`PORTAL_CONVERT_TIMEOUT_SECONDS` timeout.
