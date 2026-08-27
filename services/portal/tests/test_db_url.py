"""URL/SSL normalization for managed Postgres (Neon, Supabase)."""

from app.db.session import build_engine_args


def test_neon_style_url_translates_sslmode_and_drops_libpq_params():
    url, connect_args = build_engine_args(
        "postgresql+asyncpg://u:p@ep-x-pooler.aws.neon.tech/portal"
        "?sslmode=require&channel_binding=require"
    )
    # asyncpg would raise TypeError on either query param as a kwarg.
    assert url == "postgresql+asyncpg://u:p@ep-x-pooler.aws.neon.tech/portal"
    assert connect_args == {"ssl": "require"}


def test_plain_url_is_untouched():
    url, connect_args = build_engine_args(
        "postgresql+asyncpg://sourcerer:pw@postgres:5432/sourcerer_portal"
    )
    assert url == "postgresql+asyncpg://sourcerer:pw@postgres:5432/sourcerer_portal"
    assert connect_args == {}


def test_unknown_params_are_preserved():
    url, connect_args = build_engine_args(
        "postgresql+asyncpg://u:p@h/db?application_name=portal&sslmode=verify-full"
    )
    assert url == "postgresql+asyncpg://u:p@h/db?application_name=portal"
    assert connect_args == {"ssl": "verify-full"}


def test_non_asyncpg_url_passes_through():
    url, connect_args = build_engine_args("sqlite+aiosqlite://")
    assert url == "sqlite+aiosqlite://"
    assert connect_args == {}
