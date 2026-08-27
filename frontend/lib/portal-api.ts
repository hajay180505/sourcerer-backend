/** Portal API client — all calls are credentialed (session cookie). */

import { API_URL } from "./api";

const BASE = `${API_URL}/api/v1/portal`;

// Marks a request as coming from the SPA (not a pasted URL / curl). The content
// endpoints require it; a top-level navigation can't send a custom header.
const CLIENT_HEADER = { "X-Sourcerer-Client": "1" } as const;

/* ---------- Types ---------- */

export interface Me {
  id: string;
  email: string;
  name: string | null;
  picture: string | null;
  is_admin: boolean;
}

/** Explicit per-node setting; null inherits from the nearest-set ancestor. */
export type NodeVisibility = "public" | "private" | null;

export interface CatalogNode {
  id: string;
  parent_id: string | null;
  name: string;
  is_folder: boolean;
  mime_type: string;
  size: number | null;
  modified_time: string | null;
  path: string;
  path_ids: string;
  child_count?: number;
  /** Effective (resolved) visibility. false on a listed folder means it is a
   * bare structural container — reachable but not requestable. */
  effective_public?: boolean;
  /** Admin payloads only. */
  visibility?: NodeVisibility;
  public_count?: number;
  desc_count?: number;
}

export interface GraphData {
  root_id: string;
  truncated: boolean;
  nodes: {
    id: string;
    name: string;
    is_folder: boolean;
    ext: string;
    size: number | null;
    depth: number;
  }[];
  links: { source: string; target: string; kind: "tree" | "wiki" }[];
}

export interface RequestItem {
  node_id: string;
  name: string;
  path: string | null;
  is_folder: boolean;
}

export interface MyRequest {
  id: string;
  status: "pending" | "approved" | "denied" | "cancelled";
  requested_days: number;
  message: string | null;
  created_at: string;
  decided_at: string | null;
  items: RequestItem[];
}

export interface MyGrant {
  id: string;
  node_id: string;
  name: string;
  path: string | null;
  path_ids: string | null;
  is_folder: boolean;
  starts_at: string;
  expires_at: string;
}

export interface AdminRequest extends MyRequest {
  user: { email: string; name: string | null };
}

export interface AdminGrant {
  id: string;
  user: { id: string; email: string; name: string | null };
  node_id: string;
  name: string;
  path: string | null;
  is_folder: boolean;
  starts_at: string;
  expires_at: string;
  expired: boolean;
}

export interface SyncStatus {
  running: boolean;
  last_started_at: string | null;
  last_finished_at: string | null;
  last_error: string | null;
  node_count: number | null;
}

export interface AuditEventRow {
  id: number;
  event: string;
  email: string | null;
  node_id: string | null;
  meta: Record<string, unknown> | null;
  created_at: string;
}

export interface MeOverview {
  grants: {
    id: string;
    node_id: string;
    name: string;
    path: string | null;
    path_ids: string | null;
    is_folder: boolean;
    expires_at: string;
  }[];
  recent_views: {
    node_id: string;
    name: string | null;
    path: string | null;
    viewed_at: string;
  }[];
  latest_request: {
    id: string;
    status: "pending" | "approved" | "denied" | "cancelled";
    created_at: string;
    decided_at: string | null;
    items: number;
  } | null;
}

export interface ContentMeta {
  id: string;
  name: string;
  mime_type: string;
  size: number | null;
  path: string;
  modified_time: string | null;
  viewer: "pdf" | "md" | "text" | "image" | "video" | "office-pdf" | "gdoc-pdf" | "unsupported";
  ticket: string;
}

/* ---------- Helpers ---------- */

async function jsonOrThrow<T>(resp: Response): Promise<T> {
  if (!resp.ok) {
    let detail = `${resp.status} ${resp.statusText}`;
    try {
      const body = await resp.json();
      if (body?.detail) detail = String(body.detail);
    } catch {
      /* keep default detail */
    }
    const error = new Error(detail) as Error & { status?: number };
    error.status = resp.status;
    throw error;
  }
  return resp.json() as Promise<T>;
}

function get<T>(path: string): Promise<T> {
  return fetch(`${BASE}${path}`, {
    credentials: "include",
    cache: "no-store",
    headers: { ...CLIENT_HEADER },
  }).then((r) => jsonOrThrow<T>(r));
}

function send<T>(method: string, path: string, body?: unknown): Promise<T> {
  return fetch(`${BASE}${path}`, {
    method,
    credentials: "include",
    headers: {
      ...CLIENT_HEADER,
      ...(body !== undefined ? { "Content-Type": "application/json" } : {}),
    },
    body: body !== undefined ? JSON.stringify(body) : undefined,
  }).then((r) => jsonOrThrow<T>(r));
}

/* ---------- Auth ---------- */

export const loginUrl = `${BASE}/auth/login`;

export async function getMe(): Promise<Me | null> {
  const resp = await fetch(`${BASE}/auth/me`, {
    credentials: "include",
    cache: "no-store",
  });
  if (resp.status === 401) return null;
  return jsonOrThrow<Me>(resp);
}

export const logout = () => send<{ ok: boolean }>("POST", "/auth/logout");

/* ---------- Catalog ---------- */

export const getChildren = (parentId: string) =>
  get<{ parent: CatalogNode; children: CatalogNode[] }>(
    `/catalog/children?parent_id=${encodeURIComponent(parentId)}`
  );

export const searchCatalog = (q: string) =>
  get<{ results: CatalogNode[] }>(`/catalog/search?q=${encodeURIComponent(q)}`);

export const getGraph = (rootId: string, depth: number, includeFiles: boolean) =>
  get<GraphData>(
    `/catalog/graph?root_id=${encodeURIComponent(rootId)}&depth=${depth}&include_files=${includeFiles}`
  );

/* ---------- Requests / grants (user) ---------- */

export const createAccessRequest = (body: {
  node_ids: string[];
  requested_days: number;
  message?: string;
}) => send<MyRequest>("POST", "/requests", body);

export const getMyRequests = () => get<{ requests: MyRequest[] }>("/requests/mine");

export const cancelRequest = (id: string) =>
  send<{ ok: boolean }>("POST", `/requests/${id}/cancel`);

export const getMyGrants = () => get<{ grants: MyGrant[] }>("/grants/mine");

export const getMyOverview = () => get<MeOverview>("/me/overview");

/* ---------- Admin ---------- */

export const adminListRequests = (status = "pending") =>
  get<{ requests: AdminRequest[] }>(`/admin/requests?status=${status}`);

export const adminApprove = (
  id: string,
  body: { starts_at?: string; expires_at?: string; node_ids?: string[] }
) => send<{ ok: boolean; granted: number; expires_at: string }>(
  "POST",
  `/admin/requests/${id}/approve`,
  body
);

export const adminDeny = (id: string, reason?: string) =>
  send<{ ok: boolean }>("POST", `/admin/requests/${id}/deny`, { reason });

export const adminListGrants = (status = "active") =>
  get<{ grants: AdminGrant[] }>(`/admin/grants?status=${status}`);

export const adminPatchGrant = (id: string, expiresAt: string) =>
  send<{ ok: boolean; expires_at: string }>("PATCH", `/admin/grants/${id}`, {
    expires_at: expiresAt,
  });

export const adminRevokeGrant = (id: string) =>
  send<{ ok: boolean }>("POST", `/admin/grants/${id}/revoke`);

export const adminSetVisibility = (nodeId: string, visibility: NodeVisibility) =>
  send<{ ok: boolean; visibility: NodeVisibility }>(
    "PATCH",
    `/admin/nodes/${encodeURIComponent(nodeId)}/visibility`,
    { visibility }
  );

export const adminTriggerSync = () => send<{ started: boolean }>("POST", "/admin/sync");

export const adminSyncStatus = () => get<SyncStatus>("/admin/sync/status");

export const adminListUsers = () =>
  get<{ users: { id: string; email: string; name: string | null; last_login_at: string | null }[] }>(
    "/admin/users"
  );

/** Server-side session kill switch: invalidates all of a user's live sessions. */
export const adminRevokeSessions = (userId: string) =>
  send<{ ok: boolean }>("POST", `/admin/users/${userId}/revoke-sessions`);

export const adminAudit = (limit = 50) =>
  get<{ events: AuditEventRow[] }>(`/admin/audit?limit=${limit}`);

/* ---------- Content ---------- */

export const getContentMeta = (fileId: string) =>
  get<ContentMeta>(`/content/${fileId}/meta`);

export const contentRawUrl = (fileId: string, ticket: string) =>
  `${BASE}/content/${fileId}/raw?t=${encodeURIComponent(ticket)}`;
export const contentPdfUrl = (fileId: string, ticket: string) =>
  `${BASE}/content/${fileId}/pdf?t=${encodeURIComponent(ticket)}`;

/** Credentialed fetch of content bytes (images, pdf data, text). */
export async function fetchContent(url: string): Promise<Response> {
  const resp = await fetch(url, {
    credentials: "include",
    cache: "no-store",
    headers: { ...CLIENT_HEADER },
  });
  if (!resp.ok) {
    let detail = `${resp.status} ${resp.statusText}`;
    try {
      const body = await resp.json();
      if (body?.detail) detail = String(body.detail);
    } catch {
      /* keep default */
    }
    throw new Error(detail);
  }
  return resp;
}
