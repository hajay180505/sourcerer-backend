"""User-facing access request + grant endpoints."""

import uuid

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.db.models import AccessRequest, AccessRequestItem, AuditEvent, DriveNode
from app.deps import CurrentUser, DbSession
from app.services import audit
from app.services.access import active_grants
from app.services.visibility import load_map

router = APIRouter(prefix="/portal/requests", tags=["requests"])


class CreateRequestBody(BaseModel):
    node_ids: list[str] = Field(min_length=1, max_length=100)
    requested_days: int = Field(ge=1, le=365)
    message: str | None = Field(default=None, max_length=2000)


def _request_payload(
    req: AccessRequest, items: list[tuple[AccessRequestItem, DriveNode | None]]
) -> dict:
    return {
        "id": str(req.id),
        "status": req.status,
        "requested_days": req.requested_days,
        "message": req.message,
        "created_at": req.created_at.isoformat(),
        "decided_at": req.decided_at.isoformat() if req.decided_at else None,
        "items": [
            {
                "node_id": item.node_id,
                "name": node.name if node else "(removed from library)",
                "path": node.path_names if node else None,
                "is_folder": node.is_folder if node else False,
            }
            for item, node in items
        ],
    }


async def _items_with_nodes(
    db, request_ids: list[uuid.UUID]
) -> dict[uuid.UUID, list[tuple[AccessRequestItem, DriveNode | None]]]:
    rows = (
        await db.execute(
            select(AccessRequestItem, DriveNode)
            .outerjoin(DriveNode, DriveNode.id == AccessRequestItem.node_id)
            .where(AccessRequestItem.request_id.in_(request_ids))
        )
    ).all()
    grouped: dict[uuid.UUID, list] = {rid: [] for rid in request_ids}
    for item, node in rows:
        grouped[item.request_id].append((item, node))
    return grouped


@router.post("", status_code=201)
async def create_request(
    body: CreateRequestBody, user: CurrentUser, db: DbSession
) -> dict:
    node_ids = list(dict.fromkeys(body.node_ids))  # dedupe, keep order
    found = set(
        (
            await db.execute(select(DriveNode.id).where(DriveNode.id.in_(node_ids)))
        ).scalars()
    )
    missing = [n for n in node_ids if n not in found]
    if missing:
        raise HTTPException(status_code=400, detail=f"Unknown items: {missing[:5]}")

    # Only effectively-public material is requestable; private nodes are
    # invisible to users, so a request naming one is either stale UI state or
    # someone probing ids. Same 400 either way, no existence leak beyond
    # what `found` already confirmed.
    vmap = await load_map(db)
    not_public = [n for n in node_ids if not vmap.is_public(n)]
    if not_public:
        raise HTTPException(
            status_code=400, detail=f"Not requestable: {not_public[:5]}"
        )

    req = AccessRequest(
        user_id=user.id,
        message=body.message,
        requested_days=body.requested_days,
        status="pending",
    )
    db.add(req)
    await db.flush()
    for node_id in node_ids:
        db.add(AccessRequestItem(request_id=req.id, node_id=node_id))
    await audit.record(
        db, "request_created", user_id=user.id, meta={"request_id": str(req.id)}
    )
    await db.commit()
    items = await _items_with_nodes(db, [req.id])
    return _request_payload(req, items[req.id])


@router.get("/mine")
async def my_requests(user: CurrentUser, db: DbSession) -> dict:
    reqs = (
        (
            await db.execute(
                select(AccessRequest)
                .where(AccessRequest.user_id == user.id)
                .order_by(AccessRequest.created_at.desc())
                .limit(100)
            )
        )
        .scalars()
        .all()
    )
    grouped = await _items_with_nodes(db, [r.id for r in reqs]) if reqs else {}
    return {"requests": [_request_payload(r, grouped[r.id]) for r in reqs]}


@router.post("/{request_id}/cancel")
async def cancel_request(request_id: uuid.UUID, user: CurrentUser, db: DbSession) -> dict:
    req = (
        await db.execute(
            select(AccessRequest)
            .where(
                AccessRequest.id == request_id, AccessRequest.user_id == user.id
            )
            .with_for_update()
        )
    ).scalar_one_or_none()
    if req is None:
        raise HTTPException(status_code=404, detail="Request not found")
    if req.status != "pending":
        raise HTTPException(status_code=409, detail=f"Request is {req.status}")
    req.status = "cancelled"
    await audit.record(
        db, "request_cancelled", user_id=user.id, meta={"request_id": str(req.id)}
    )
    await db.commit()
    return {"ok": True}


# Participant home dashboard: one call for grants, recent views, and the
# latest request's status.
me_router = APIRouter(prefix="/portal/me", tags=["requests"])


@me_router.get("/overview")
async def my_overview(user: CurrentUser, db: DbSession) -> dict:
    pairs = await active_grants(db, user)

    # Last viewed materials: newest 'content_viewed' audit rows, deduped by
    # node, joined to the (possibly swept) catalog.
    view_rows = (
        await db.execute(
            select(AuditEvent.node_id, AuditEvent.created_at)
            .where(
                AuditEvent.user_id == user.id,
                AuditEvent.event == "content_viewed",
            )
            .order_by(AuditEvent.created_at.desc())
            .limit(30)
        )
    ).all()
    recent: list[tuple[str, object]] = []
    seen: set[str] = set()
    for node_id, viewed_at in view_rows:
        if node_id and node_id not in seen:
            seen.add(node_id)
            recent.append((node_id, viewed_at))
        if len(recent) == 5:
            break
    nodes = {
        n.id: n
        for n in (
            await db.execute(
                select(DriveNode).where(DriveNode.id.in_([r[0] for r in recent]))
            )
        ).scalars()
    }

    latest_request = (
        await db.execute(
            select(AccessRequest)
            .where(AccessRequest.user_id == user.id)
            .order_by(AccessRequest.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    latest_items = 0
    if latest_request:
        latest_items = len(
            (
                await db.execute(
                    select(AccessRequestItem.id).where(
                        AccessRequestItem.request_id == latest_request.id
                    )
                )
            ).all()
        )

    return {
        "grants": [
            {
                "id": str(grant.id),
                "node_id": grant.node_id,
                "name": node.name if node else "(removed from library)",
                "path": node.path_names if node else None,
                "path_ids": node.path_ids if node else None,
                "is_folder": node.is_folder if node else False,
                "expires_at": grant.expires_at.isoformat(),
            }
            for grant, node in pairs
        ],
        "recent_views": [
            {
                "node_id": node_id,
                "name": nodes[node_id].name if node_id in nodes else None,
                "path": nodes[node_id].path_names if node_id in nodes else None,
                "viewed_at": viewed_at.isoformat(),
            }
            for node_id, viewed_at in recent
            if node_id in nodes
        ],
        "latest_request": (
            {
                "id": str(latest_request.id),
                "status": latest_request.status,
                "created_at": latest_request.created_at.isoformat(),
                "decided_at": (
                    latest_request.decided_at.isoformat()
                    if latest_request.decided_at
                    else None
                ),
                "items": latest_items,
            }
            if latest_request
            else None
        ),
    }


# Grants live under /portal/requests' sibling prefix for the user view.
grants_router = APIRouter(prefix="/portal/grants", tags=["requests"])


@grants_router.get("/mine")
async def my_grants(user: CurrentUser, db: DbSession) -> dict:
    pairs = await active_grants(db, user)
    return {
        "grants": [
            {
                "id": str(grant.id),
                "node_id": grant.node_id,
                "name": node.name if node else "(removed from library)",
                "path": node.path_names if node else None,
                "path_ids": node.path_ids if node else None,
                "is_folder": node.is_folder if node else False,
                "starts_at": grant.starts_at.isoformat(),
                "expires_at": grant.expires_at.isoformat(),
            }
            for grant, node in pairs
        ]
    }
