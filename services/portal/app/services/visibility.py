"""Effective-visibility resolution for the public/private catalog layer.

Every node carries a tri-state ``visibility`` (public / private / NULL =
inherit). A node's *effective* visibility is its own explicit setting, else the
nearest explicitly-set ancestor's, else private — nothing is public until the
admin marks it.

What a regular user may see:
- effectively-public nodes (browsable, searchable, requestable);
- nodes covered by one of their active grants (a grant overrides visibility);
- "structural" ancestors of either — private folders rendered as bare
  containers so the visible items stay reachable in the tree. Structural nodes
  are never requestable and never match search.

The whole catalog skeleton (id, parent_id, visibility) is loaded and resolved
in memory — a few-column scan over ~5–20k rows, cheaper and far simpler than
expressing nearest-ancestor logic in dialect-portable SQL. The resolved map is
cached per engine for a short TTL (and invalidated on every visibility toggle
and after catalog sync), so browsing doesn't re-scan the catalog per click.
"""

import time
from collections import defaultdict
from datetime import datetime, timezone
from weakref import WeakKeyDictionary

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import DriveNode, Grant, User


def _resolve_inherited(
    parent: dict[str, str | None],
    positive: dict[str, bool],
) -> set[str]:
    """Nodes whose nearest self-or-ancestor entry in ``positive`` is True.

    ``positive`` maps node id -> verdict for nodes that decide the walk
    (explicit visibility, or membership in a grant set); nodes absent from it
    inherit. Unresolved roots default to False (private / not covered).
    """
    memo: dict[str, bool] = {}
    for start in parent:
        chain: list[str] = []
        cur: str | None = start
        verdict = False
        while cur is not None:
            if cur in memo:
                verdict = memo[cur]
                break
            if cur in positive:
                verdict = positive[cur]
                memo[cur] = verdict
                break
            chain.append(cur)
            cur = parent.get(cur)
        for node_id in chain:
            memo[node_id] = verdict
    return {node_id for node_id, ok in memo.items() if ok}


def _ancestors_of(parent: dict[str, str | None], members: set[str]) -> set[str]:
    """Ancestors of ``members`` that are not members themselves."""
    out: set[str] = set()
    for node_id in members:
        cur = parent.get(node_id)
        while cur is not None and cur not in out and cur not in members:
            out.add(cur)
            cur = parent.get(cur)
    return out


class VisibilityMap:
    """Grant-independent view: effective visibility + admin descendant counts."""

    def __init__(self, rows: list[tuple[str, str | None, str | None]]):
        self.parent: dict[str, str | None] = {}
        self.children: dict[str, list[str]] = defaultdict(list)
        explicit: dict[str, bool] = {}
        for node_id, parent_id, visibility in rows:
            self.parent[node_id] = parent_id
            if parent_id is not None:
                self.children[parent_id].append(node_id)
            if visibility is not None:
                explicit[node_id] = visibility == "public"
        self.public: set[str] = _resolve_inherited(self.parent, explicit)
        # Private folders that contain something public (bare tree containers).
        self.structural: set[str] = _ancestors_of(self.parent, self.public)

        self._counts: dict[str, tuple[int, int]] | None = None

    def is_public(self, node_id: str) -> bool:
        return node_id in self.public

    def descendant_counts(self) -> dict[str, tuple[int, int]]:
        """node id -> (effectively-public descendants, total descendants).

        Iterative post-order so a deep tree can't hit the recursion limit.
        Memoized on the map, which is itself cached and rebuilt on any change.
        """
        if self._counts is not None:
            return self._counts
        counts: dict[str, tuple[int, int]] = {}
        roots = [n for n, p in self.parent.items() if p is None or p not in self.parent]
        stack: list[tuple[str, bool]] = [(r, False) for r in roots]
        while stack:
            node_id, expanded = stack.pop()
            kids = self.children.get(node_id, [])
            if not expanded:
                stack.append((node_id, True))
                stack.extend((k, False) for k in kids)
                continue
            pub = total = 0
            for kid in kids:
                kid_pub, kid_total = counts[kid]
                pub += kid_pub + (1 if kid in self.public else 0)
                total += kid_total + 1
            counts[node_id] = (pub, total)
        self._counts = counts
        return counts


class UserVisibility:
    """Per-user view: effective visibility plus the user's grant overrides."""

    def __init__(self, vmap: VisibilityMap, granted_ids: set[str]):
        self.vmap = vmap
        present = {g: True for g in granted_ids if g in vmap.parent}
        self.covered: set[str] = _resolve_inherited(vmap.parent, present)
        grant_structural = _ancestors_of(vmap.parent, self.covered)
        self._visible: set[str] = (
            vmap.public | vmap.structural | self.covered | grant_structural
        )

    def visible(self, node_id: str) -> bool:
        return node_id in self._visible

    def is_public(self, node_id: str) -> bool:
        return self.vmap.is_public(node_id)

    def visible_child_count(self, folder_id: str) -> int:
        return sum(1 for c in self.vmap.children.get(folder_id, ()) if self.visible(c))


# Resolved maps keyed weakly by engine (prod: one engine, one entry; tests get
# a fresh engine per fixture, so entries never leak across test databases).
_MAP_TTL_SECONDS = 30.0
_map_cache: "WeakKeyDictionary[object, tuple[float, VisibilityMap]]" = (
    WeakKeyDictionary()
)


def invalidate_cache() -> None:
    """Drop cached maps; called on visibility toggles and after catalog sync."""
    _map_cache.clear()


async def load_map(db: AsyncSession) -> VisibilityMap:
    engine = db.get_bind()
    hit = _map_cache.get(engine)
    if hit is not None and time.monotonic() - hit[0] < _MAP_TTL_SECONDS:
        return hit[1]
    rows = (
        await db.execute(
            select(DriveNode.id, DriveNode.parent_id, DriveNode.visibility)
        )
    ).all()
    vmap = VisibilityMap([tuple(r) for r in rows])
    _map_cache[engine] = (time.monotonic(), vmap)
    return vmap


async def load_user_view(db: AsyncSession, user: User) -> UserVisibility:
    now = datetime.now(timezone.utc)
    granted_ids = set(
        (
            await db.execute(
                select(Grant.node_id).where(
                    Grant.user_id == user.id,
                    Grant.status == "active",
                    Grant.starts_at <= now,
                    Grant.expires_at >= now,
                )
            )
        ).scalars()
    )
    return UserVisibility(await load_map(db), granted_ids)
