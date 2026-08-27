"""Admin endpoints: request approval/denial, grant management, sync, audit."""

import asyncio
import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Literal

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.db.models import (
    AccessRequest,
    AccessRequestItem,
    AuditEvent,
    DriveNode,
    Grant,
    User,
)
from app.db.session import SessionLocal
from app.deps import CurrentAdmin, DbSession
from app.services import audit, catalog_sync, visibility

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/portal/admin", tags=["admin"])


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(dt: datetime | None) -> datetime | None:
    """Coerce a client-supplied datetime to tz-aware UTC. Pydantic yields a
    naive datetime for ISO strings without an offset (e.g. from Swagger or an
    ad-hoc script); comparing that to _utcnow() would raise TypeError → 500."""
    if dt is not None and dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


# Retain references to detached background tasks so the event loop can't
# garbage-collect a running sync mid-flight (which would wedge status.running).
_background_tasks: set[asyncio.Task] = set()


class ApproveBody(BaseModel):
    starts_at: datetime | None = None
    expires_at: datetime | None = None  # default: starts_at + requested_days
    node_ids: list[str] | None = Field(default=None, max_length=100)  # subset


class DenyBody(BaseModel):
    reason: str | None = Field(default=None, max_length=2000)


class PatchGrantBody(BaseModel):
    expires_at: datetime


class VisibilityBody(BaseModel):
    # None = clear the override and inherit from the nearest-set ancestor.
    visibility: Literal["public", "private"] | None


@router.get("/requests")
async def list_requests(
    _: CurrentAdmin,
    db: DbSession,
    status: str = Query(default="pending"),
) -> dict:
    reqs = (
        (
            await db.execute(
                select(AccessRequest, User)
                .join(User, User.id == AccessRequest.user_id)
                .where(AccessRequest.status == status)
                .order_by(AccessRequest.created_at.desc())
                .limit(200)
            )
        )
        .all()
    )
    request_ids = [r.id for r, _u in reqs]
    items_rows = (
        (
            await db.execute(
                select(AccessRequestItem, DriveNode)
                .outerjoin(DriveNode, DriveNode.id == AccessRequestItem.node_id)
                .where(AccessRequestItem.request_id.in_(request_ids))
            )
        ).all()
        if request_ids
        else []
    )
    grouped: dict[uuid.UUID, list] = {rid: [] for rid in request_ids}
    for item, node in items_rows:
        grouped[item.request_id].append(
            {
                "node_id": item.node_id,
                "name": node.name if node else "(removed from library)",
                "path": node.path_names if node else None,
                "is_folder": node.is_folder if node else False,
            }
        )
    return {
        "requests": [
            {
                "id": str(req.id),
                "status": req.status,
                "requested_days": req.requested_days,
                "message": req.message,
                "created_at": req.created_at.isoformat(),
                "user": {"email": user.email, "name": user.name},
                "items": grouped[req.id],
            }
            for req, user in reqs
        ]
    }


@router.post("/requests/{request_id}/approve")
async def approve_request(
    request_id: uuid.UUID, body: ApproveBody, admin: CurrentAdmin, db: DbSession
) -> dict:
    req = (
        await db.execute(
            select(AccessRequest)
            .where(AccessRequest.id == request_id)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if req is None:
        raise HTTPException(status_code=404, detail="Request not found")
    if req.status != "pending":
        raise HTTPException(status_code=409, detail=f"Request is {req.status}")

    item_ids = list(
        (
            await db.execute(
                select(AccessRequestItem.node_id).where(
                    AccessRequestItem.request_id == req.id
                )
            )
        ).scalars()
    )
    # `is None` distinguishes "unset -> grant everything requested" from an
    # explicit empty selection, which must grant nothing (400 below).
    node_ids = item_ids if body.node_ids is None else body.node_ids
    unknown = [n for n in node_ids if n not in set(item_ids)]
    if unknown:
        raise HTTPException(status_code=400, detail=f"Not in request: {unknown[:5]}")
    if not node_ids:
        raise HTTPException(status_code=400, detail="No items to grant")

    starts_at = _as_utc(body.starts_at) or _utcnow()
    expires_at = _as_utc(body.expires_at) or starts_at + timedelta(
        days=req.requested_days
    )
    if expires_at <= starts_at:
        raise HTTPException(status_code=400, detail="expires_at must be after starts_at")

    for node_id in node_ids:
        db.add(
            Grant(
                user_id=req.user_id,
                node_id=node_id,
                request_id=req.id,
                starts_at=starts_at,
                expires_at=expires_at,
                status="active",
                granted_by=admin.id,
            )
        )
    req.status = "approved"
    req.decided_at = _utcnow()
    req.decided_by = admin.id
    await audit.record(
        db,
        "request_approved",
        user_id=admin.id,
        meta={
            "request_id": str(req.id),
            "node_ids": node_ids,
            "expires_at": expires_at.isoformat(),
        },
    )
    await db.commit()
    return {"ok": True, "granted": len(node_ids), "expires_at": expires_at.isoformat()}


@router.post("/requests/{request_id}/deny")
async def deny_request(
    request_id: uuid.UUID, body: DenyBody, admin: CurrentAdmin, db: DbSession
) -> dict:
    req = (
        await db.execute(
            select(AccessRequest)
            .where(AccessRequest.id == request_id)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if req is None:
        raise HTTPException(status_code=404, detail="Request not found")
    if req.status != "pending":
        raise HTTPException(status_code=409, detail=f"Request is {req.status}")
    req.status = "denied"
    req.decided_at = _utcnow()
    req.decided_by = admin.id
    await audit.record(
        db,
        "request_denied",
        user_id=admin.id,
        meta={"request_id": str(req.id), "reason": body.reason},
    )
    await db.commit()
    return {"ok": True}


@router.get("/grants")
async def list_grants(
    _: CurrentAdmin,
    db: DbSession,
    status: str = Query(default="active"),
    user_id: uuid.UUID | None = None,
) -> dict:
    query = (
        select(Grant, User, DriveNode)
        .join(User, User.id == Grant.user_id)
        .outerjoin(DriveNode, DriveNode.id == Grant.node_id)
        .where(Grant.status == status)
        .order_by(Grant.expires_at)
        .limit(500)
    )
    if user_id:
        query = query.where(Grant.user_id == user_id)
    rows = (await db.execute(query)).all()
    now = _utcnow()
    return {
        "grants": [
            {
                "id": str(grant.id),
                "user": {"id": str(user.id), "email": user.email, "name": user.name},
                "node_id": grant.node_id,
                "name": node.name if node else "(removed from library)",
                "path": node.path_names if node else None,
                "is_folder": node.is_folder if node else False,
                "starts_at": grant.starts_at.isoformat(),
                "expires_at": grant.expires_at.isoformat(),
                "expired": grant.expires_at < now,
            }
            for grant, user, node in rows
        ]
    }


@router.patch("/grants/{grant_id}")
async def patch_grant(
    grant_id: uuid.UUID, body: PatchGrantBody, admin: CurrentAdmin, db: DbSession
) -> dict:
    grant = (
        await db.execute(select(Grant).where(Grant.id == grant_id))
    ).scalar_one_or_none()
    if grant is None:
        raise HTTPException(status_code=404, detail="Grant not found")
    if grant.status != "active":
        raise HTTPException(status_code=409, detail=f"Grant is {grant.status}")
    new_expiry = _as_utc(body.expires_at)
    if new_expiry <= _as_utc(grant.starts_at):
        raise HTTPException(
            status_code=400, detail="expires_at must be after the grant start"
        )
    grant.expires_at = new_expiry
    await audit.record(
        db,
        "grant_updated",
        user_id=admin.id,
        node_id=grant.node_id,
        meta={"grant_id": str(grant.id), "expires_at": grant.expires_at.isoformat()},
    )
    await db.commit()
    return {"ok": True, "expires_at": grant.expires_at.isoformat()}


@router.post("/grants/{grant_id}/revoke")
async def revoke_grant(grant_id: uuid.UUID, admin: CurrentAdmin, db: DbSession) -> dict:
    grant = (
        await db.execute(select(Grant).where(Grant.id == grant_id))
    ).scalar_one_or_none()
    if grant is None:
        raise HTTPException(status_code=404, detail="Grant not found")
    if grant.status == "revoked":
        raise HTTPException(status_code=409, detail="Already revoked")
    grant.status = "revoked"
    grant.revoked_at = _utcnow()
    await audit.record(
        db,
        "grant_revoked",
        user_id=admin.id,
        node_id=grant.node_id,
        meta={"grant_id": str(grant.id)},
    )
    await db.commit()
    return {"ok": True}


@router.patch("/nodes/{node_id}/visibility")
async def set_visibility(
    node_id: str, body: VisibilityBody, admin: CurrentAdmin, db: DbSession
) -> dict:
    """Set or clear a node's explicit visibility. Inheritance is live: a folder
    set public exposes its whole subtree except explicitly-private descendants,
    including files that sync in later. Existing grants are never touched —
    flipping to private only stops new discovery/requests."""
    node = (
        await db.execute(select(DriveNode).where(DriveNode.id == node_id))
    ).scalar_one_or_none()
    if node is None:
        raise HTTPException(status_code=404, detail="Node not found")
    previous = node.visibility
    node.visibility = body.visibility
    await audit.record(
        db,
        "visibility_changed",
        user_id=admin.id,
        node_id=node.id,
        meta={
            "visibility": body.visibility,
            "previous": previous,
            "path": node.path_names,
        },
    )
    await db.commit()
    visibility.invalidate_cache()
    return {"ok": True, "visibility": node.visibility}


@router.get("/users")
async def list_users(_: CurrentAdmin, db: DbSession) -> dict:
    rows = (
        (
            await db.execute(
                select(User).order_by(User.last_login_at.desc().nullslast()).limit(500)
            )
        )
        .scalars()
        .all()
    )
    return {
        "users": [
            {
                "id": str(u.id),
                "email": u.email,
                "name": u.name,
                "last_login_at": (
                    u.last_login_at.isoformat() if u.last_login_at else None
                ),
            }
            for u in rows
        ]
    }


@router.post("/users/{user_id}/revoke-sessions")
async def revoke_user_sessions(
    user_id: uuid.UUID, admin: CurrentAdmin, db: DbSession
) -> dict:
    """Invalidate all of a user's active sessions (server-side kill switch)."""
    user = (
        await db.execute(select(User).where(User.id == user_id))
    ).scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    user.session_version += 1
    await audit.record(
        db,
        "sessions_revoked",
        user_id=admin.id,
        meta={"target_user_id": str(user.id)},
    )
    await db.commit()
    return {"ok": True}


@router.get("/audit")
async def list_audit(
    _: CurrentAdmin,
    db: DbSession,
    user_id: uuid.UUID | None = None,
    limit: int = Query(default=100, ge=1, le=500),
) -> dict:
    query = (
        select(AuditEvent, User)
        .outerjoin(User, User.id == AuditEvent.user_id)
        .order_by(AuditEvent.created_at.desc())
        .limit(limit)
    )
    if user_id:
        query = query.where(AuditEvent.user_id == user_id)
    rows = (await db.execute(query)).all()
    return {
        "events": [
            {
                "id": event.id,
                "event": event.event,
                "email": user.email if user else None,
                "node_id": event.node_id,
                "meta": event.meta,
                "created_at": event.created_at.isoformat(),
            }
            for event, user in rows
        ]
    }


@router.post("/sync", status_code=202)
async def trigger_sync(_: CurrentAdmin) -> dict:
    """Kick off a catalog sync in the background; 409 if one is running."""
    if catalog_sync.status["running"]:
        raise HTTPException(status_code=409, detail="Sync already running")

    async def _run() -> None:
        try:
            await catalog_sync.run_sync(SessionLocal)
        except Exception:  # already logged inside run_sync
            pass

    task = asyncio.get_running_loop().create_task(_run())
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)
    return {"started": True}


@router.get("/sync/status")
async def sync_status(_: CurrentAdmin) -> dict:
    return catalog_sync.status
