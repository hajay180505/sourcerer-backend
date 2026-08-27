"""Portal ORM models.

Design notes:
- ``drive_nodes`` keys on the immutable Google Drive fileId, so grants survive
  renames/moves; the sync job rewrites materialized paths on every run.
- ``path_ids`` is a materialized ancestor path ("/rootId/aId/selfId/") — a
  folder grant covers a node iff the node's path_ids starts with the granted
  node's path_ids. One indexed LIKE instead of a recursive walk.
- Enums use non-native VARCHAR + CHECK so the schema is portable to SQLite
  (unit tests run on aiosqlite).
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


REQUEST_STATUSES = ("pending", "approved", "denied", "cancelled")
GRANT_STATUSES = ("active", "revoked")
VISIBILITIES = ("public", "private")


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    google_sub: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    email: Mapped[str] = mapped_column(Text, unique=True, nullable=False)  # lowercased
    name: Mapped[str | None] = mapped_column(Text)
    picture_url: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Bumped on logout / admin revoke; the session JWT carries the value it was
    # minted with, and current_user rejects tokens whose value is stale. This is
    # the server-side kill switch for stateless sessions.
    session_version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )


class DriveNode(Base):
    """Synced Drive catalog — metadata only, never content."""

    __tablename__ = "drive_nodes"

    id: Mapped[str] = mapped_column(Text, primary_key=True)  # Drive fileId
    parent_id: Mapped[str | None] = mapped_column(
        ForeignKey("drive_nodes.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    mime_type: Mapped[str] = mapped_column(Text, nullable=False)
    is_folder: Mapped[bool] = mapped_column(Boolean, nullable=False)
    size: Mapped[int | None] = mapped_column(BigInteger)  # null for Google-native
    modified_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    md5_checksum: Mapped[str | None] = mapped_column(Text)
    path_ids: Mapped[str] = mapped_column(Text, nullable=False)  # "/root/a/self/"
    path_names: Mapped[str] = mapped_column(Text, nullable=False)
    depth: Mapped[int] = mapped_column(Integer, nullable=False)
    synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # Admin-set tri-state; NULL inherits from the nearest explicitly-set
    # ancestor (default: private). Deliberately not written by catalog sync so
    # settings survive re-syncs; a swept node loses its setting, which fails
    # safe (back to private).
    visibility: Mapped[str | None] = mapped_column(
        Enum(*VISIBILITIES, name="node_visibility", native_enum=False, length=8)
    )

    __table_args__ = (
        # text_pattern_ops so `path_ids LIKE 'prefix%'` uses the index on Postgres.
        Index(
            "ix_drive_nodes_path_ids",
            "path_ids",
            postgresql_ops={"path_ids": "text_pattern_ops"},
        ),
    )


class MdLink(Base):
    """Obsidian [[wikilink]] edge between two markdown catalog nodes."""

    __tablename__ = "md_links"

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True
    )
    source_id: Mapped[str] = mapped_column(
        ForeignKey("drive_nodes.id", ondelete="CASCADE"), nullable=False, index=True
    )
    target_id: Mapped[str] = mapped_column(
        ForeignKey("drive_nodes.id", ondelete="CASCADE"), nullable=False, index=True
    )
    link_text: Mapped[str] = mapped_column(Text, nullable=False)

    __table_args__ = (UniqueConstraint("source_id", "target_id"),)


class AccessRequest(Base):
    __tablename__ = "access_requests"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    message: Mapped[str | None] = mapped_column(Text)
    requested_days: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(
        Enum(*REQUEST_STATUSES, name="request_status", native_enum=False, length=16),
        nullable=False,
        default="pending",
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decided_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))


class AccessRequestItem(Base):
    __tablename__ = "access_request_items"

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True
    )
    request_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("access_requests.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # No FK: a node may be swept from the catalog after the request is made.
    node_id: Mapped[str] = mapped_column(Text, nullable=False)

    __table_args__ = (UniqueConstraint("request_id", "node_id"),)


class Grant(Base):
    """Timed access to a node; a folder grant covers its whole subtree."""

    __tablename__ = "grants"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    # No FK: grants must survive catalog sweeps; a grant on a node that no
    # longer exists simply matches nothing during access resolution.
    node_id: Mapped[str] = mapped_column(Text, nullable=False)
    request_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("access_requests.id"))
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(
        Enum(*GRANT_STATUSES, name="grant_status", native_enum=False, length=16),
        nullable=False,
        default="active",
    )
    granted_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (Index("ix_grants_user_status", "user_id", "status", "expires_at"),)


class AuditEvent(Base):
    __tablename__ = "audit_events"

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), index=True)
    event: Mapped[str] = mapped_column(Text, nullable=False)
    node_id: Mapped[str | None] = mapped_column(Text)  # no FK: survives node sweep
    meta: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, index=True
    )
