"""Async engine / session factory for the portal database."""

from collections.abc import AsyncGenerator
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from sourcerer_core.config import settings

# libpq query params that asyncpg does not accept as connection kwargs. Managed
# Postgres (Neon, Supabase) hands out URLs carrying them, and asyncpg raises
# TypeError on the unknown keyword rather than ignoring it.
_LIBPQ_ONLY = {"sslmode", "channel_binding", "sslrootcert", "sslcert", "sslkey"}


def build_engine_args(url: str) -> tuple[str, dict[str, Any]]:
    """Return (url, connect_args) for an asyncpg URL.

    Strips libpq-only query params and translates `sslmode` into asyncpg's
    `ssl` connect arg, so a managed-Postgres connection string
    (`...?sslmode=require&channel_binding=require`) works verbatim. Non-asyncpg
    URLs (sqlite in tests, psycopg) are passed through untouched.
    """
    parsed = urlsplit(url)
    if "asyncpg" not in parsed.scheme:
        return url, {}

    connect_args: dict[str, Any] = {}
    kept: list[tuple[str, str]] = []
    for key, value in parse_qsl(parsed.query, keep_blank_values=True):
        if key == "sslmode":
            # asyncpg understands the libpq mode names as a string.
            connect_args["ssl"] = value
        elif key in _LIBPQ_ONLY:
            continue  # asyncpg negotiates channel binding / uses system CAs
        else:
            kept.append((key, value))
    return urlunsplit(parsed._replace(query=urlencode(kept))), connect_args


_url, _connect_args = build_engine_args(settings.DATABASE_URL)

engine = create_async_engine(
    _url,
    pool_pre_ping=True,  # managed PG scales to zero; drop dead conns silently
    connect_args=_connect_args,
)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency yielding one session per request."""
    async with SessionLocal() as session:
        yield session
