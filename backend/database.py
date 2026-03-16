import os
import asyncio
import re
from contextlib import asynccontextmanager
from typing import Any

try:
    import psycopg
    from psycopg.rows import dict_row
    from psycopg_pool import AsyncConnectionPool
except Exception:  # pragma: no cover
    psycopg = None  # type: ignore
    dict_row = None  # type: ignore
    AsyncConnectionPool = None  # type: ignore

_pool: Any = None


def _require_driver() -> None:
    if psycopg is None or AsyncConnectionPool is None:
        raise RuntimeError(
            "psycopg is not installed. Install backend dependencies to use Postgres."
        )


def _database_url() -> str:
    url = os.environ.get("DATABASE_URL", "").strip()
    if not url:
        raise RuntimeError("DATABASE_URL is not set")
    return url


def _pool_min_size() -> int:
    return int(os.environ.get("PG_POOL_MIN", "1"))


def _pool_max_size() -> int:
    return int(os.environ.get("PG_POOL_MAX", "10"))


def _statement_timeout_ms() -> int:
    return int(os.environ.get("PG_STATEMENT_TIMEOUT_MS", "30000"))


def _connect_retries() -> int:
    return int(os.environ.get("PG_CONNECT_RETRIES", "8"))


def _connect_retry_delay_s() -> float:
    return float(os.environ.get("PG_CONNECT_RETRY_DELAY_S", "0.5"))


async def _configure_connection(conn) -> None:
    # Force UTC at the DB connection level. We still accept/emit timestamptz.
    async with conn.cursor() as cur:
        tz = (os.environ.get("PG_TIMEZONE") or "Asia/Kolkata").strip() or "Asia/Kolkata"
        if not re.fullmatch(r"[A-Za-z0-9_\/+\-]+", tz):
            tz = "Asia/Kolkata"
        await cur.execute(f"SET TIME ZONE '{tz}'")
        statement_timeout_ms = _statement_timeout_ms()
        await cur.execute(f"SET statement_timeout = {statement_timeout_ms}")


async def init_db_pool():
    _require_driver()
    global _pool
    if _pool is not None:
        return _pool

    retries = _connect_retries()
    delay_s = _connect_retry_delay_s()
    last_exc: Exception | None = None
    for attempt in range(retries):
        try:
            conninfo = _database_url()
            _pool = AsyncConnectionPool(
                conninfo=conninfo,
                min_size=_pool_min_size(),
                max_size=_pool_max_size(),
                timeout=float(os.environ.get("PG_COMMAND_TIMEOUT", "30")),
                kwargs={"autocommit": True, "row_factory": dict_row},
                configure=_configure_connection,
                open=False,
            )
            await _pool.open()
            return _pool
        except Exception as e:
            last_exc = e
            if attempt < retries - 1:
                await asyncio.sleep(delay_s)
                continue
            break

    raise RuntimeError(f"Failed to connect to Postgres after {retries} attempt(s): {last_exc}")


def get_pool():
    _require_driver()
    if _pool is None:
        raise RuntimeError("DB pool not initialized. Call init_db_pool() first")
    return _pool


async def close_db_pool() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


async def ping() -> bool:
    try:
        row = await fetchrow("SELECT 1 AS ok")
        return bool(row and row.get("ok") == 1)
    except Exception:
        return False


async def fetch_conn(conn, query: str, *args):
    async with conn.cursor() as cur:
        await cur.execute(query, args)
        rows = await cur.fetchall()
        return rows


async def fetchrow_conn(conn, query: str, *args):
    async with conn.cursor() as cur:
        await cur.execute(query, args)
        row = await cur.fetchone()
        return row


async def execute_conn(conn, query: str, *args):
    async with conn.cursor() as cur:
        await cur.execute(query, args)
        return cur.statusmessage or ""


async def executemany_conn(conn, query: str, args_list: list[tuple]):
    async with conn.cursor() as cur:
        await cur.executemany(query, args_list)
        return None


@asynccontextmanager
async def transaction():
    pool = get_pool()
    async with pool.connection() as conn:
        await conn.set_autocommit(False)
        try:
            yield conn
            await conn.commit()
        except Exception:
            await conn.rollback()
            raise
        finally:
            await conn.set_autocommit(True)


async def fetch(query: str, *args):
    pool = get_pool()
    async with pool.connection() as conn:
        return await fetch_conn(conn, query, *args)


async def fetchrow(query: str, *args):
    pool = get_pool()
    async with pool.connection() as conn:
        return await fetchrow_conn(conn, query, *args)


async def execute(query: str, *args):
    pool = get_pool()
    async with pool.connection() as conn:
        return await execute_conn(conn, query, *args)


async def executemany(query: str, args_list: list[tuple]):
    pool = get_pool()
    async with pool.connection() as conn:
        return await executemany_conn(conn, query, args_list)
