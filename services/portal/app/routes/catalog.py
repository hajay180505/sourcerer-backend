"""Catalog browsing endpoints — metadata only, opaque node ids, no content.

Non-admin responses are filtered through the visibility layer: users see
effectively-public nodes, nodes their active grants cover, and the bare
"structural" folders needed to reach them. Admins see everything, plus the
explicit visibility setting and public-descendant counts for the toggles.
"""

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import func, or_, select

from app.db.models import DriveNode, MdLink
from app.deps import CurrentUser, DbSession
from app.services.security import is_admin_email
from app.services.visibility import load_map, load_user_view
from sourcerer_core.config import settings

router = APIRouter(prefix="/portal/catalog", tags=["catalog"])

_GRAPH_NODE_CAP = 1500
_LIKE_ESCAPE = "\\"


def _like_escape(value: str) -> str:
    """Neutralize LIKE metacharacters so `value` matches literally (used with
    `escape='\\'`). Without this, a search for `100%` or an id-prefix containing
    `_` would behave as a wildcard."""
    return (
        value.replace(_LIKE_ESCAPE, _LIKE_ESCAPE + _LIKE_ESCAPE)
        .replace("%", _LIKE_ESCAPE + "%")
        .replace("_", _LIKE_ESCAPE + "_")
    )


def _node_payload(
    node: DriveNode,
    child_count: int | None = None,
    extra: dict | None = None,
) -> dict:
    payload = {
        "id": node.id,
        "parent_id": node.parent_id,
        "name": node.name,
        "is_folder": node.is_folder,
        "mime_type": node.mime_type,
        "size": node.size,
        "modified_time": (
            node.modified_time.isoformat() if node.modified_time else None
        ),
        "path": node.path_names,
        "path_ids": node.path_ids,
    }
    if child_count is not None:
        payload["child_count"] = child_count
    if extra:
        payload.update(extra)
    return payload


async def _resolve_root(db) -> DriveNode:
    root = (
        await db.execute(
            select(DriveNode).where(DriveNode.id == settings.PORTAL_ROOT_FOLDER_ID)
        )
    ).scalar_one_or_none()
    if root is None:
        raise HTTPException(status_code=503, detail="Catalog not synced yet")
    return root


@router.get("/children")
async def children(
    user: CurrentUser,
    db: DbSession,
    parent_id: str = Query(default="root"),
) -> dict:
    """One level of the tree (lazy loading). parent_id='root' for the top."""
    parent = (
        await _resolve_root(db)
        if parent_id == "root"
        else (
            await db.execute(select(DriveNode).where(DriveNode.id == parent_id))
        ).scalar_one_or_none()
    )
    if parent is None:
        raise HTTPException(status_code=404, detail="Folder not found")

    rows = (
        (
            await db.execute(
                select(DriveNode)
                .where(DriveNode.parent_id == parent.id)
                .order_by(DriveNode.is_folder.desc(), func.lower(DriveNode.name))
            )
        )
        .scalars()
        .all()
    )

    if is_admin_email(user.email):
        vmap = await load_map(db)
        desc = vmap.descendant_counts()

        def admin_extra(n: DriveNode) -> dict:
            extra = {
                "visibility": n.visibility,
                "effective_public": vmap.is_public(n.id),
            }
            if n.is_folder:
                pub, total = desc.get(n.id, (0, 0))
                extra["public_count"] = pub
                extra["desc_count"] = total
            return extra

        return {
            "parent": _node_payload(parent, extra=admin_extra(parent)),
            "children": [
                _node_payload(
                    n,
                    len(vmap.children.get(n.id, ())) if n.is_folder else None,
                    admin_extra(n),
                )
                for n in rows
            ],
        }

    view = await load_user_view(db, user)
    # The root is always browsable as a container (it just lists nothing when
    # the library holds no public/granted material); any other folder 404s
    # rather than reveal that it exists.
    if parent.id != settings.PORTAL_ROOT_FOLDER_ID and not view.visible(parent.id):
        raise HTTPException(status_code=404, detail="Folder not found")

    visible_rows = [n for n in rows if view.visible(n.id)]
    return {
        "parent": _node_payload(
            parent, extra={"effective_public": view.is_public(parent.id)}
        ),
        "children": [
            _node_payload(
                n,
                view.visible_child_count(n.id) if n.is_folder else None,
                {"effective_public": view.is_public(n.id)},
            )
            for n in visible_rows
        ],
    }


_SEARCH_LIMIT = 50
# Overfetch for non-admins so visibility filtering doesn't starve the results.
_SEARCH_SCAN_LIMIT = 500


@router.get("/search")
async def search(
    user: CurrentUser,
    db: DbSession,
    q: str = Query(min_length=2, max_length=100),
) -> dict:
    admin = is_admin_email(user.email)
    pattern = f"%{_like_escape(q)}%"
    rows = (
        (
            await db.execute(
                select(DriveNode)
                .where(
                    or_(
                        DriveNode.name.ilike(pattern, escape=_LIKE_ESCAPE),
                        DriveNode.path_names.ilike(pattern, escape=_LIKE_ESCAPE),
                    )
                )
                .order_by(DriveNode.is_folder.desc(), DriveNode.depth)
                .limit(_SEARCH_LIMIT if admin else _SEARCH_SCAN_LIMIT)
            )
        )
        .scalars()
        .all()
    )
    if not admin:
        # Public or grant-covered only — structural containers would clutter
        # search and leak private folder names without being openable.
        view = await load_user_view(db, user)
        rows = [
            n
            for n in rows
            if view.is_public(n.id) or n.id in view.covered
        ][:_SEARCH_LIMIT]
    return {"results": [_node_payload(n) for n in rows]}


@router.get("/graph")
async def graph(
    user: CurrentUser,
    db: DbSession,
    root_id: str = Query(default="root"),
    depth: int = Query(default=2, ge=1, le=4),
    include_files: bool = Query(default=True),
) -> dict:
    """Subtree graph for the Obsidian-style view.

    Scoped by root + relative depth and capped at _GRAPH_NODE_CAP nodes so the
    22k-file library never lands in the browser at once; the UI expands by
    re-querying with a deeper root.
    """
    admin = is_admin_email(user.email)
    view = None if admin else await load_user_view(db, user)

    root = (
        await _resolve_root(db)
        if root_id == "root"
        else (
            await db.execute(select(DriveNode).where(DriveNode.id == root_id))
        ).scalar_one_or_none()
    )
    if root is None:
        raise HTTPException(status_code=404, detail="Node not found")
    if view and root.id != settings.PORTAL_ROOT_FOLDER_ID and not view.visible(root.id):
        raise HTTPException(status_code=404, detail="Node not found")

    query = (
        select(DriveNode)
        .where(
            DriveNode.path_ids.like(
                f"{_like_escape(root.path_ids)}%", escape=_LIKE_ESCAPE
            ),
            DriveNode.depth <= root.depth + depth,
        )
        .order_by(DriveNode.depth)
        # Overfetch for non-admins: visibility filtering happens after the
        # query, so the cap must apply to the filtered set.
        .limit(_GRAPH_NODE_CAP + 1 if admin else (_GRAPH_NODE_CAP + 1) * 10)
    )
    if not include_files:
        query = query.where(DriveNode.is_folder.is_(True))
    rows = (await db.execute(query)).scalars().all()
    if view:
        rows = [n for n in rows if view.visible(n.id)]
    truncated = len(rows) > _GRAPH_NODE_CAP
    rows = rows[:_GRAPH_NODE_CAP]
    ids = {n.id for n in rows}

    links = [
        {"source": n.parent_id, "target": n.id, "kind": "tree"}
        for n in rows
        if n.parent_id in ids
    ]
    wiki_rows = (
        await db.execute(
            select(MdLink.source_id, MdLink.target_id).where(
                MdLink.source_id.in_(ids), MdLink.target_id.in_(ids)
            )
        )
    ).all()
    links += [
        {"source": s, "target": t, "kind": "wiki"} for s, t in wiki_rows
    ]

    def ext(name: str) -> str:
        return name.rsplit(".", 1)[-1].lower() if "." in name else ""

    return {
        "root_id": root.id,
        "truncated": truncated,
        "nodes": [
            {
                "id": n.id,
                "name": n.name,
                "is_folder": n.is_folder,
                "ext": "" if n.is_folder else ext(n.name),
                "size": n.size,
                "depth": n.depth - root.depth,
            }
            for n in rows
        ],
        "links": links,
    }
