"""Catalog sync: Drive metadata -> drive_nodes (upsert + mark-and-sweep).

Never touches file contents. Safe to run repeatedly; grants reference
immutable Drive IDs so renames/moves are handled by path rewriting here.
"""

import asyncio
import logging
import re
from datetime import datetime, timezone

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import DriveNode, MdLink
from app.services import visibility
from app.services.gdrive import NodeRecord, download_file_bytes, walk_folder_metadata
from sourcerer_core.config import settings

logger = logging.getLogger(__name__)

_sync_lock = asyncio.Lock()

status: dict = {
    "running": False,
    "last_started_at": None,
    "last_finished_at": None,
    "last_error": None,
    "node_count": None,
}


def _parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _is_md(name: str) -> bool:
    return name.lower().endswith(".md")


async def upsert_records(
    db: AsyncSession, records: list[NodeRecord], run_started_at: datetime
) -> list[str]:
    """Upsert walked records and sweep rows the walk no longer saw.

    Dialect-portable (Postgres prod, SQLite tests): prefetch existing rows in
    chunks, update in place, insert the rest, then delete stale rows.
    Returns the ids of markdown files that are new or content-changed (their
    wikilinks need re-extraction).
    """
    changed_md: list[str] = []
    chunk_size = 1000
    for start in range(0, len(records), chunk_size):
        chunk = records[start : start + chunk_size]
        ids = [r["id"] for r in chunk]
        existing = {
            n.id: n
            for n in (
                await db.execute(select(DriveNode).where(DriveNode.id.in_(ids)))
            ).scalars()
        }
        for rec in chunk:
            if _is_md(rec["name"]):
                prior = existing.get(rec["id"])
                if prior is None or prior.md5_checksum != rec["md5_checksum"]:
                    changed_md.append(rec["id"])
            values = dict(
                parent_id=rec["parent_id"],
                name=rec["name"],
                mime_type=rec["mime_type"],
                is_folder=rec["is_folder"],
                size=rec["size"],
                modified_time=_parse_time(rec["modified_time"]),
                md5_checksum=rec["md5_checksum"],
                path_ids=rec["path_ids"],
                path_names=rec["path_names"],
                depth=rec["depth"],
                synced_at=run_started_at,
            )
            node = existing.get(rec["id"])
            if node is None:
                db.add(DriveNode(id=rec["id"], **values))
            else:
                for key, value in values.items():
                    setattr(node, key, value)
        await db.flush()

    # Children of removed parents also have stale stamps, so a plain sweep is
    # enough (the self-FK CASCADE is just a safety net).
    await db.execute(delete(DriveNode).where(DriveNode.synced_at < run_started_at))
    await db.commit()
    # New/moved/swept nodes change effective visibility; rebuild on next read.
    visibility.invalidate_cache()
    return changed_md


_WIKILINK_RE = re.compile(r"\[\[([^\]|#\n]+)")


async def sync_wikilinks(db: AsyncSession, changed_md_ids: list[str]) -> int:
    """Extract Obsidian [[wikilinks]] from new/changed markdown files.

    Resolution follows Obsidian's shortest-path semantics: the link's last
    path segment matches a file basename case-insensitively (md files match
    with or without the .md extension; other files need the full name).
    Only changed sources are re-fetched — text downloads are tiny and free.
    """
    if not changed_md_ids:
        return 0

    # basename (lowercase) -> node id, for all catalog files.
    rows = (
        await db.execute(
            select(DriveNode.id, DriveNode.name).where(DriveNode.is_folder.is_(False))
        )
    ).all()
    by_name: dict[str, str] = {}
    for node_id, name in rows:
        lowered = name.lower()
        by_name.setdefault(lowered, node_id)
        if lowered.endswith(".md"):
            by_name.setdefault(lowered[:-3], node_id)

    total = 0
    for source_id in changed_md_ids:
        try:
            text = (await asyncio.to_thread(download_file_bytes, source_id)).decode(
                "utf-8", errors="ignore"
            )
        except Exception:
            logger.warning("Could not fetch md file %s for link extraction", source_id)
            continue
        await db.execute(delete(MdLink).where(MdLink.source_id == source_id))
        seen_targets: set[str] = set()
        for match in _WIKILINK_RE.finditer(text):
            link_text = match.group(1).strip()
            basename = link_text.split("/")[-1].strip().lower()
            target_id = by_name.get(basename) or by_name.get(f"{basename}.md")
            if target_id and target_id != source_id and target_id not in seen_targets:
                seen_targets.add(target_id)
                db.add(
                    MdLink(source_id=source_id, target_id=target_id, link_text=link_text)
                )
                total += 1
    await db.commit()
    return total


async def run_sync(db_factory) -> dict:
    """Run one full catalog sync. Raises RuntimeError if already running."""
    if _sync_lock.locked():
        raise RuntimeError("Sync already running")
    async with _sync_lock:
        run_started_at = datetime.now(timezone.utc)
        status.update(
            running=True, last_started_at=run_started_at.isoformat(), last_error=None
        )
        try:
            records = await asyncio.to_thread(
                walk_folder_metadata, settings.PORTAL_ROOT_FOLDER_ID
            )
            async with db_factory() as db:
                changed_md = await upsert_records(db, records, run_started_at)
                link_count = await sync_wikilinks(db, changed_md)
            status.update(node_count=len(records))
            logger.info(
                "Catalog sync complete: %d nodes, %d new wikilinks from %d md files",
                len(records),
                link_count,
                len(changed_md),
            )
        except Exception as exc:
            status.update(last_error=f"{type(exc).__name__}: {exc}")
            logger.exception("Catalog sync failed")
            raise
        finally:
            status.update(
                running=False,
                last_finished_at=datetime.now(timezone.utc).isoformat(),
            )
    return status


async def periodic_sync_loop(db_factory) -> None:
    """Lifespan background task: initial sync if empty, then interval loop."""
    try:
        async with db_factory() as db:
            empty = (
                await db.execute(select(DriveNode.id).limit(1))
            ).scalar_one_or_none() is None
        if empty:
            logger.info("Catalog empty — running initial sync")
            await run_sync(db_factory)
    except Exception:
        logger.exception("Initial catalog sync failed; will retry on interval")

    interval = settings.PORTAL_SYNC_INTERVAL_MINUTES * 60
    while True:
        await asyncio.sleep(interval)
        try:
            await run_sync(db_factory)
        except RuntimeError:
            pass  # manual sync in flight
        except Exception:
            logger.exception("Periodic catalog sync failed")
