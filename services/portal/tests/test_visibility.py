"""Public/private visibility: resolver semantics + catalog/request filtering."""

import uuid

import pytest
from fastapi import HTTPException

from app.db.models import AuditEvent, Grant
from app.routes.admin import VisibilityBody, set_visibility
from app.routes.catalog import children, graph, search
from app.routes.requests import CreateRequestBody, create_request
from app.services.visibility import (
    UserVisibility,
    VisibilityMap,
    load_map,
    load_user_view,
)
from sourcerer_core.config import settings
from tests.conftest import make_node, make_user, utc


def _grant(user, node_id):
    return Grant(
        id=uuid.uuid4(),
        user_id=user.id,
        node_id=node_id,
        starts_at=utc(-1),
        expires_at=utc(7),
        status="active",
        granted_by=user.id,
        created_at=utc(),
    )


async def _seed(db):
    """
    root                      (inherit -> private, but always browsable)
    ├── pub/       [public]
    │   ├── a.pdf             (inherits public)
    │   ├── b.pdf  [private]  (explicit override)
    │   └── sub/   [private]
    │       └── hidden.pdf    (inherits private)
    ├── sec/                  (inherit -> private, structural via gem.md)
    │   ├── gem.md [public]
    │   └── dark.pdf
    ├── vault/                (inherit -> private, nothing public inside)
    │   └── v.pdf
    └── loose.pdf             (inherit -> private)
    """
    root = make_node("root", None, "Library", is_folder=True)
    pub = make_node("pub", root, "Public Stuff", is_folder=True)
    pub.visibility = "public"
    a = make_node("a", pub, "a.pdf")
    b = make_node("b", pub, "b.pdf")
    b.visibility = "private"
    sub = make_node("sub", pub, "Sub", is_folder=True)
    sub.visibility = "private"
    hidden = make_node("hidden", sub, "hidden.pdf")
    sec = make_node("sec", root, "Secret", is_folder=True)
    gem = make_node("gem", sec, "gem.md")
    gem.visibility = "public"
    dark = make_node("dark", sec, "dark.pdf")
    vault = make_node("vault", root, "Vault", is_folder=True)
    v = make_node("v", vault, "v.pdf")
    loose = make_node("loose", root, "loose.pdf")
    user = make_user()
    db.add_all([root, pub, a, b, sub, hidden, sec, gem, dark, vault, v, loose, user])
    await db.commit()
    return user


# ---------- Resolver ----------


async def test_default_is_private(db):
    await _seed(db)
    vmap = await load_map(db)
    for node_id in ("root", "sec", "dark", "vault", "v", "loose"):
        assert not vmap.is_public(node_id), node_id


async def test_public_folder_covers_descendants(db):
    await _seed(db)
    vmap = await load_map(db)
    assert vmap.is_public("pub")
    assert vmap.is_public("a")


async def test_explicit_private_overrides_public_ancestor(db):
    await _seed(db)
    vmap = await load_map(db)
    assert not vmap.is_public("b")
    assert not vmap.is_public("sub")
    assert not vmap.is_public("hidden")  # inherits sub's private, not pub's public


async def test_public_file_in_private_folder_marks_ancestors_structural(db):
    await _seed(db)
    vmap = await load_map(db)
    assert vmap.is_public("gem")
    assert "sec" in vmap.structural
    assert "root" in vmap.structural
    assert "vault" not in vmap.structural  # nothing public inside


async def test_descendant_counts(db):
    await _seed(db)
    counts = (await load_map(db)).descendant_counts()
    assert counts["root"] == (3, 11)  # pub, a, gem public of 11 descendants
    assert counts["pub"] == (1, 4)  # a public; b, sub, hidden private
    assert counts["sec"] == (1, 2)
    assert counts["vault"] == (0, 1)


async def test_grant_overrides_visibility_and_exposes_ancestors(db):
    user = await _seed(db)
    db.add(_grant(user, "vault"))
    await db.commit()
    view = await load_user_view(db, user)
    assert view.visible("vault") and view.visible("v")
    assert not view.is_public("v")  # visible, but still not requestable


async def test_expired_grant_gives_no_visibility(db):
    user = await _seed(db)
    grant = _grant(user, "vault")
    grant.expires_at = utc(-1)
    db.add(grant)
    await db.commit()
    view = await load_user_view(db, user)
    assert not view.visible("vault")


async def test_grant_on_swept_node_is_ignored(db):
    user = await _seed(db)
    db.add(_grant(user, "gone"))
    await db.commit()
    view = await load_user_view(db, user)
    assert not view.visible("vault")


def test_empty_catalog():
    view = UserVisibility(VisibilityMap([]), set())
    assert not view.visible("anything")


# ---------- Catalog endpoints ----------


@pytest.fixture
def as_root(monkeypatch):
    monkeypatch.setattr(settings, "PORTAL_ROOT_FOLDER_ID", "root")


async def test_children_filters_for_users(db, as_root):
    user = await _seed(db)
    result = await children(user, db, parent_id="root")
    names = {c["id"]: c for c in result["children"]}
    assert set(names) == {"pub", "sec"}  # vault + loose invisible
    assert names["pub"]["effective_public"] is True
    assert names["sec"]["effective_public"] is False  # structural container
    assert names["pub"]["child_count"] == 1  # only a.pdf visible inside
    assert names["sec"]["child_count"] == 1  # only gem.md


async def test_children_of_public_folder_hides_private_overrides(db, as_root):
    user = await _seed(db)
    result = await children(user, db, parent_id="pub")
    assert {c["id"] for c in result["children"]} == {"a"}


async def test_children_of_invisible_folder_is_404(db, as_root):
    user = await _seed(db)
    with pytest.raises(HTTPException) as exc:
        await children(user, db, parent_id="vault")
    assert exc.value.status_code == 404


async def test_children_of_granted_private_folder_works(db, as_root):
    user = await _seed(db)
    db.add(_grant(user, "vault"))
    await db.commit()
    result = await children(user, db, parent_id="vault")
    assert {c["id"] for c in result["children"]} == {"v"}


async def test_children_admin_sees_everything_with_toggles(db, as_root, monkeypatch):
    user = await _seed(db)
    monkeypatch.setattr(settings, "ADMIN_EMAILS", user.email)
    result = await children(user, db, parent_id="root")
    names = {c["id"]: c for c in result["children"]}
    assert set(names) == {"pub", "sec", "vault", "loose"}
    assert names["pub"]["visibility"] == "public"
    assert names["sec"]["visibility"] is None
    assert names["sec"]["effective_public"] is False
    assert names["pub"]["public_count"] == 1
    assert names["pub"]["desc_count"] == 4


async def test_search_returns_public_and_granted_only(db, as_root):
    user = await _seed(db)
    assert {n["id"] for n in (await search(user, db, q="pdf"))["results"]} == {"a"}
    assert (await search(user, db, q="dark"))["results"] == []
    # The structural folder itself never matches (gem.md may, via its path).
    assert "sec" not in {n["id"] for n in (await search(user, db, q="Secret"))["results"]}
    db.add(_grant(user, "dark"))
    await db.commit()
    assert {n["id"] for n in (await search(user, db, q="dark"))["results"]} == {"dark"}


async def test_graph_filters_for_users(db, as_root):
    user = await _seed(db)
    data = await graph(user, db, root_id="root", depth=4, include_files=True)
    ids = {n["id"] for n in data["nodes"]}
    assert ids == {"root", "pub", "a", "sec", "gem"}
    for link in data["links"]:
        assert link["source"] in ids and link["target"] in ids


async def test_graph_root_must_be_visible(db, as_root):
    user = await _seed(db)
    with pytest.raises(HTTPException) as exc:
        await graph(user, db, root_id="vault", depth=2, include_files=True)
    assert exc.value.status_code == 404


# ---------- Requests ----------


async def test_request_for_private_node_rejected(db):
    user = await _seed(db)
    body = CreateRequestBody(node_ids=["dark"], requested_days=7)
    with pytest.raises(HTTPException) as exc:
        await create_request(body, user, db)
    assert exc.value.status_code == 400
    assert "Not requestable" in exc.value.detail


async def test_request_for_public_nodes_accepted(db):
    user = await _seed(db)
    body = CreateRequestBody(node_ids=["pub", "gem"], requested_days=7)
    result = await create_request(body, user, db)
    assert result["status"] == "pending"
    assert {i["node_id"] for i in result["items"]} == {"pub", "gem"}


# ---------- Admin toggle ----------


async def test_set_and_clear_visibility(db):
    user = await _seed(db)
    await set_visibility("vault", VisibilityBody(visibility="public"), user, db)
    assert (await load_map(db)).is_public("v")
    await set_visibility("vault", VisibilityBody(visibility=None), user, db)
    assert not (await load_map(db)).is_public("v")
    events = (
        await db.execute(
            AuditEvent.__table__.select().where(
                AuditEvent.event == "visibility_changed"
            )
        )
    ).all()
    assert len(events) == 2


async def test_set_visibility_unknown_node_404(db):
    user = await _seed(db)
    with pytest.raises(HTTPException) as exc:
        await set_visibility("nope", VisibilityBody(visibility="public"), user, db)
    assert exc.value.status_code == 404
