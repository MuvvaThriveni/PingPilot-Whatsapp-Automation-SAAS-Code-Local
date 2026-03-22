"""Retention – Archive Engine + Archive Purge.

Phase 2: Moves rows older than RETENTION_DAYS from live tables into archive
tables using safe, batched, transactional processing.

Phase 4: Deletes rows older than PURGE_RETENTION_DAYS from archive tables
using the same batched, transactional approach.

Sequence per archive run:
  1. Aggregate daily_message_stats (MUST happen before messages are deleted)
  2. Archive messages      → messages_archive
  3. Archive chat_messages → chat_messages_archive
  4. Archive usage_events  → usage_events_archive
  5. Archive webhook_events → webhook_events_archive

Sequence per purge run (Phase 4):
  1. Purge messages_archive
  2. Purge chat_messages_archive
  3. Purge usage_events_archive
  4. Purge webhook_events_archive

Usage (manual CLI):
    python retention.py              # archive only
    python retention.py --purge      # archive + purge
    python retention.py --purge-only # purge only
"""

from __future__ import annotations

import os
import sys
import time
import asyncio
from datetime import datetime, timedelta, timezone
from contextlib import asynccontextmanager

from dotenv import load_dotenv

load_dotenv()

from database import (
    init_db_pool,
    close_db_pool,
    get_pool,
    fetch_conn,
    execute_conn,
)
from observability import log_event

# ── Configuration ────────────────────────────────────────────────────────────

RETENTION_DAYS: int = int(os.environ.get("RETENTION_DAYS", "2"))
BATCH_SIZE: int = int(os.environ.get("RETENTION_BATCH_SIZE", "1000"))
MAX_BATCHES_PER_RUN: int = int(os.environ.get("RETENTION_MAX_BATCHES", "100"))
BATCH_SLEEP_SEC: float = float(os.environ.get("RETENTION_BATCH_SLEEP", "0.05"))

# Longer timeout for retention queries (default 120s vs normal 30s)
RETENTION_STATEMENT_TIMEOUT_MS: int = int(
    os.environ.get("RETENTION_STATEMENT_TIMEOUT_MS", "120000")
)

# ── Purge Configuration (Phase 4) ───────────────────────────────────────────

PURGE_ENABLED: bool = os.environ.get(
    "PURGE_ENABLED", "false"
).strip().lower() in ("1", "true", "yes")
PURGE_RETENTION_DAYS: int = int(os.environ.get("PURGE_RETENTION_DAYS", "90"))
PURGE_BATCH_SIZE: int = int(os.environ.get("PURGE_BATCH_SIZE", "1000"))
PURGE_MAX_BATCHES: int = int(os.environ.get("PURGE_MAX_BATCHES", "50"))
PURGE_BATCH_SLEEP_SEC: float = float(os.environ.get("PURGE_BATCH_SLEEP", "0.05"))

# ── Table definitions ────────────────────────────────────────────────────────

# Tables with BIGSERIAL `id` as primary key
_ID_TABLES = [
    {
        "source": "messages",
        "archive": "messages_archive",
        "columns": (
            "id, tenant_id, direction, product_type, contact_phone, contact_name, "
            "message_text, message_type, wa_message_id, status, template_name, "
            "campaign_id, media_id, error_message, created_at, updated_at"
        ),
        "pk": "id",
    },
    {
        "source": "chat_messages",
        "archive": "chat_messages_archive",
        "columns": (
            "id, tenant_id, contact_phone, contact_name, "
            "message_text, direction, created_at"
        ),
        "pk": "id",
    },
    {
        "source": "usage_events",
        "archive": "usage_events_archive",
        "columns": (
            "id, tenant_id, event_type, product_type, campaign_id, "
            "contact_phone, billable, month_key, created_at"
        ),
        "pk": "id",
    },
]

# webhook_events uses composite PK (tenant_id, event_id) — handled separately
_WEBHOOK_TABLE = {
    "source": "webhook_events",
    "archive": "webhook_events_archive",
    "columns": "tenant_id, event_id, event_type, status, created_at, processed_at",
}


# ── Helper: transactional connection with extended timeout ───────────────────

@asynccontextmanager
async def _retention_txn():
    """Yield a transactional connection with extended statement_timeout."""
    pool = get_pool()
    async with pool.connection() as conn:
        await conn.set_autocommit(False)
        try:
            async with conn.cursor() as cur:
                await cur.execute(
                    f"SET LOCAL statement_timeout = {RETENTION_STATEMENT_TIMEOUT_MS}"
                )
            yield conn
            await conn.commit()
        except Exception:
            await conn.rollback()
            raise
        finally:
            await conn.set_autocommit(True)


# ── Step 1: Aggregate daily stats BEFORE archiving messages ──────────────────

async def _aggregate_daily_stats(cutoff_iso: str) -> int:
    """Pre-aggregate message counts for days about to be archived.

    Uses GREATEST in ON CONFLICT so counts never decrease on re-runs.
    Returns the number of rows upserted (informational).
    """
    t0 = time.perf_counter()
    try:
        async with _retention_txn() as conn:
            result = await execute_conn(
                conn,
                """
                INSERT INTO daily_message_stats
                    (tenant_id, stat_date, product_type, direction, status, message_count)
                SELECT
                    tenant_id,
                    (created_at AT TIME ZONE 'Asia/Kolkata')::date AS stat_date,
                    product_type,
                    direction,
                    status,
                    COUNT(*) AS message_count
                FROM messages
                WHERE created_at < %s::timestamptz
                GROUP BY tenant_id, stat_date, product_type, direction, status
                ON CONFLICT (tenant_id, stat_date, product_type, direction, status)
                DO UPDATE SET
                    message_count = GREATEST(
                        daily_message_stats.message_count,
                        EXCLUDED.message_count
                    ),
                    created_at = now()
                """,
                cutoff_iso,
            )
            # Parse "INSERT 0 N" status message
            count = 0
            if result:
                parts = str(result).split()
                if len(parts) >= 3 and parts[2].isdigit():
                    count = int(parts[2])

            elapsed = (time.perf_counter() - t0) * 1000
            log_event(
                "retention_aggregate",
                status="ok",
                detail=f"upserted={count} rows",
                duration_ms=elapsed,
            )
            return count
    except Exception as e:
        elapsed = (time.perf_counter() - t0) * 1000
        log_event(
            "retention_aggregate",
            status="error",
            detail=str(e)[:120],
            level="ERROR",
            duration_ms=elapsed,
        )
        raise


# ── Step 2: Batch archive for id-based tables ───────────────────────────────

async def _archive_id_table(table_cfg: dict, cutoff_iso: str) -> int:
    """Archive rows from a BIGSERIAL-PK table in batches.

    Each batch: SELECT FOR UPDATE SKIP LOCKED → INSERT archive → DELETE source.
    Returns total rows moved.
    """
    source = table_cfg["source"]
    archive = table_cfg["archive"]
    columns = table_cfg["columns"]
    pk = table_cfg["pk"]

    total_moved = 0

    for batch_num in range(1, MAX_BATCHES_PER_RUN + 1):
        t0 = time.perf_counter()
        try:
            async with _retention_txn() as conn:
                # 1. Lock batch of oldest rows
                rows = await fetch_conn(
                    conn,
                    f"SELECT {pk} FROM {source} "
                    f"WHERE created_at < %s::timestamptz "
                    f"ORDER BY {pk} ASC LIMIT %s "
                    f"FOR UPDATE SKIP LOCKED",
                    cutoff_iso,
                    BATCH_SIZE,
                )

                if not rows:
                    break

                ids = [r[pk] for r in rows]
                batch_count = len(ids)

                # 2. Copy into archive (ON CONFLICT = idempotent)
                await execute_conn(
                    conn,
                    f"INSERT INTO {archive} ({columns}, archived_at) "
                    f"SELECT {columns}, now() FROM {source} "
                    f"WHERE {pk} = ANY(%s) "
                    f"ON CONFLICT DO NOTHING",
                    ids,
                )

                # 3. Remove from live table
                await execute_conn(
                    conn,
                    f"DELETE FROM {source} WHERE {pk} = ANY(%s)",
                    ids,
                )

            total_moved += batch_count
            elapsed = (time.perf_counter() - t0) * 1000
            log_event(
                "retention_batch",
                status="ok",
                detail=f"{source}: batch={batch_num} rows={batch_count} total={total_moved}",
                duration_ms=elapsed,
            )
        except Exception as e:
            elapsed = (time.perf_counter() - t0) * 1000
            log_event(
                "retention_batch",
                status="error",
                level="ERROR",
                detail=f"{source}: batch={batch_num} error={str(e)[:100]}",
                duration_ms=elapsed,
            )
            raise

        # Breathe between batches to reduce DB load
        if batch_num < MAX_BATCHES_PER_RUN:
            await asyncio.sleep(BATCH_SLEEP_SEC)

    return total_moved


# ── Step 3: Batch archive for webhook_events (composite PK) ─────────────────

async def _archive_webhook_events(cutoff_iso: str) -> int:
    """Archive webhook_events in batches. Composite PK (tenant_id, event_id).

    Returns total rows moved.
    """
    cfg = _WEBHOOK_TABLE
    source = cfg["source"]
    archive = cfg["archive"]
    columns = cfg["columns"]

    total_moved = 0

    for batch_num in range(1, MAX_BATCHES_PER_RUN + 1):
        t0 = time.perf_counter()
        try:
            async with _retention_txn() as conn:
                # 1. Lock batch
                rows = await fetch_conn(
                    conn,
                    f"SELECT tenant_id, event_id FROM {source} "
                    f"WHERE created_at < %s::timestamptz "
                    f"ORDER BY created_at ASC LIMIT %s "
                    f"FOR UPDATE SKIP LOCKED",
                    cutoff_iso,
                    BATCH_SIZE,
                )

                if not rows:
                    break

                batch_count = len(rows)
                tenant_ids = [r["tenant_id"] for r in rows]
                event_ids = [r["event_id"] for r in rows]

                # 2. Copy into archive (idempotent)
                await execute_conn(
                    conn,
                    f"INSERT INTO {archive} ({columns}, archived_at) "
                    f"SELECT {columns}, now() FROM {source} "
                    f"WHERE (tenant_id, event_id) IN ("
                    f"  SELECT unnest(%s::text[]), unnest(%s::text[])"
                    f") ON CONFLICT DO NOTHING",
                    tenant_ids,
                    event_ids,
                )

                # 3. Remove from live table
                await execute_conn(
                    conn,
                    f"DELETE FROM {source} "
                    f"WHERE (tenant_id, event_id) IN ("
                    f"  SELECT unnest(%s::text[]), unnest(%s::text[])"
                    f")",
                    tenant_ids,
                    event_ids,
                )

            total_moved += batch_count
            elapsed = (time.perf_counter() - t0) * 1000
            log_event(
                "retention_batch",
                status="ok",
                detail=f"{source}: batch={batch_num} rows={batch_count} total={total_moved}",
                duration_ms=elapsed,
            )
        except Exception as e:
            elapsed = (time.perf_counter() - t0) * 1000
            log_event(
                "retention_batch",
                status="error",
                level="ERROR",
                detail=f"{source}: batch={batch_num} error={str(e)[:100]}",
                duration_ms=elapsed,
            )
            raise

        if batch_num < MAX_BATCHES_PER_RUN:
            await asyncio.sleep(BATCH_SLEEP_SEC)

    return total_moved


# ── Main orchestrator ────────────────────────────────────────────────────────

async def archive_old_data() -> dict:
    """Archive data older than RETENTION_DAYS from all live tables.

    Sequence:
      1. Aggregate daily_message_stats  (preserves dashboard counts)
      2. Archive messages               (webhook fallback already in place)
      3. Archive chat_messages
      4. Archive usage_events
      5. Archive webhook_events

    Returns summary dict with row counts per table.
    """
    cutoff = (datetime.now(timezone.utc) - timedelta(days=RETENTION_DAYS)).isoformat()

    t0 = time.perf_counter()
    log_event(
        "retention_start",
        detail=(
            f"cutoff={cutoff} retention_days={RETENTION_DAYS} "
            f"batch_size={BATCH_SIZE} max_batches={MAX_BATCHES_PER_RUN}"
        ),
    )

    summary: dict[str, int] = {}

    try:
        # Step 1: Aggregate BEFORE deleting any messages
        agg_count = await _aggregate_daily_stats(cutoff)
        summary["daily_message_stats_upserted"] = agg_count

        # Step 2: Archive id-based tables (messages first — most critical)
        for table_cfg in _ID_TABLES:
            moved = await _archive_id_table(table_cfg, cutoff)
            summary[table_cfg["source"]] = moved

        # Step 3: Archive webhook_events (composite PK)
        moved = await _archive_webhook_events(cutoff)
        summary[_WEBHOOK_TABLE["source"]] = moved

        elapsed = (time.perf_counter() - t0) * 1000
        log_event(
            "retention_complete",
            status="ok",
            detail=str(summary),
            duration_ms=elapsed,
        )
    except Exception as e:
        elapsed = (time.perf_counter() - t0) * 1000
        log_event(
            "retention_complete",
            status="error",
            level="ERROR",
            detail=f"partial={summary} error={str(e)[:100]}",
            duration_ms=elapsed,
        )
        raise

    return summary


# ── Phase 4: Purge old archive rows ──────────────────────────────────────────

# Archive tables with BIGSERIAL `id` PK
_ARCHIVE_ID_TABLES = [t["archive"] for t in _ID_TABLES]
# Archive table with composite PK
_ARCHIVE_WEBHOOK = _WEBHOOK_TABLE["archive"]


async def _purge_id_archive(table: str, cutoff_iso: str) -> int:
    """Delete old rows from an id-PK archive table in batches.

    Each batch: SELECT FOR UPDATE SKIP LOCKED → DELETE.
    Returns total rows deleted.
    """
    total_deleted = 0

    for batch_num in range(1, PURGE_MAX_BATCHES + 1):
        t0 = time.perf_counter()
        try:
            async with _retention_txn() as conn:
                rows = await fetch_conn(
                    conn,
                    f"SELECT id FROM {table} "
                    f"WHERE archived_at < %s::timestamptz "
                    f"ORDER BY id ASC LIMIT %s "
                    f"FOR UPDATE SKIP LOCKED",
                    cutoff_iso,
                    PURGE_BATCH_SIZE,
                )

                if not rows:
                    break

                ids = [r["id"] for r in rows]
                batch_count = len(ids)

                await execute_conn(
                    conn,
                    f"DELETE FROM {table} WHERE id = ANY(%s)",
                    ids,
                )

            total_deleted += batch_count
            elapsed = (time.perf_counter() - t0) * 1000
            log_event(
                "purge_batch",
                status="ok",
                detail=f"{table}: batch={batch_num} rows={batch_count} total={total_deleted}",
                duration_ms=elapsed,
            )
        except Exception as e:
            elapsed = (time.perf_counter() - t0) * 1000
            log_event(
                "purge_batch",
                status="error",
                level="ERROR",
                detail=f"{table}: batch={batch_num} error={str(e)[:100]}",
                duration_ms=elapsed,
            )
            raise

        if batch_num < PURGE_MAX_BATCHES:
            await asyncio.sleep(PURGE_BATCH_SLEEP_SEC)

    return total_deleted


async def _purge_webhook_archive(cutoff_iso: str) -> int:
    """Delete old rows from webhook_events_archive (composite PK) in batches.

    Returns total rows deleted.
    """
    table = _ARCHIVE_WEBHOOK
    total_deleted = 0

    for batch_num in range(1, PURGE_MAX_BATCHES + 1):
        t0 = time.perf_counter()
        try:
            async with _retention_txn() as conn:
                rows = await fetch_conn(
                    conn,
                    f"SELECT tenant_id, event_id FROM {table} "
                    f"WHERE archived_at < %s::timestamptz "
                    f"ORDER BY archived_at ASC LIMIT %s "
                    f"FOR UPDATE SKIP LOCKED",
                    cutoff_iso,
                    PURGE_BATCH_SIZE,
                )

                if not rows:
                    break

                batch_count = len(rows)
                tenant_ids = [r["tenant_id"] for r in rows]
                event_ids = [r["event_id"] for r in rows]

                await execute_conn(
                    conn,
                    f"DELETE FROM {table} "
                    f"WHERE (tenant_id, event_id) IN ("
                    f"  SELECT unnest(%s::text[]), unnest(%s::text[])"
                    f")",
                    tenant_ids,
                    event_ids,
                )

            total_deleted += batch_count
            elapsed = (time.perf_counter() - t0) * 1000
            log_event(
                "purge_batch",
                status="ok",
                detail=f"{table}: batch={batch_num} rows={batch_count} total={total_deleted}",
                duration_ms=elapsed,
            )
        except Exception as e:
            elapsed = (time.perf_counter() - t0) * 1000
            log_event(
                "purge_batch",
                status="error",
                level="ERROR",
                detail=f"{table}: batch={batch_num} error={str(e)[:100]}",
                duration_ms=elapsed,
            )
            raise

        if batch_num < PURGE_MAX_BATCHES:
            await asyncio.sleep(PURGE_BATCH_SLEEP_SEC)

    return total_deleted


async def purge_old_archives() -> dict:
    """Delete archive rows older than PURGE_RETENTION_DAYS.

    Sequence:
      1. Purge messages_archive
      2. Purge chat_messages_archive
      3. Purge usage_events_archive
      4. Purge webhook_events_archive

    Returns summary dict with deleted row counts per table.
    """
    cutoff = (
        datetime.now(timezone.utc) - timedelta(days=PURGE_RETENTION_DAYS)
    ).isoformat()

    t0 = time.perf_counter()
    log_event(
        "purge_started",
        detail=(
            f"cutoff={cutoff} purge_retention_days={PURGE_RETENTION_DAYS} "
            f"batch_size={PURGE_BATCH_SIZE} max_batches={PURGE_MAX_BATCHES}"
        ),
    )

    summary: dict[str, int] = {}

    try:
        # Purge id-based archive tables
        for table in _ARCHIVE_ID_TABLES:
            deleted = await _purge_id_archive(table, cutoff)
            summary[table] = deleted

        # Purge webhook_events_archive (composite PK)
        deleted = await _purge_webhook_archive(cutoff)
        summary[_ARCHIVE_WEBHOOK] = deleted

        elapsed = (time.perf_counter() - t0) * 1000
        total_rows = sum(summary.values())
        log_event(
            "purge_completed",
            status="ok",
            detail=f"total_deleted={total_rows} {summary}",
            duration_ms=elapsed,
        )
    except Exception as e:
        elapsed = (time.perf_counter() - t0) * 1000
        log_event(
            "purge_failed",
            status="error",
            level="ERROR",
            detail=f"partial={summary} error={str(e)[:100]}",
            duration_ms=elapsed,
        )
        raise

    return summary


# ── CLI entry point ──────────────────────────────────────────────────────────

async def _main():
    run_purge = "--purge" in sys.argv or "--purge-only" in sys.argv
    purge_only = "--purge-only" in sys.argv

    await init_db_pool()
    try:
        if not purge_only:
            print(f"[retention] Starting archive run  (RETENTION_DAYS={RETENTION_DAYS})")
            print(f"[retention] BATCH_SIZE={BATCH_SIZE}  MAX_BATCHES={MAX_BATCHES_PER_RUN}")
            print()
            summary = await archive_old_data()
            print()
            print("=== Archive Summary ===")
            for table, count in summary.items():
                print(f"  {table}: {count} rows")
            print("=======================")

        if run_purge:
            print()
            print(f"[purge] Starting purge run  (PURGE_RETENTION_DAYS={PURGE_RETENTION_DAYS})")
            print(f"[purge] BATCH_SIZE={PURGE_BATCH_SIZE}  MAX_BATCHES={PURGE_MAX_BATCHES}")
            print()
            purge_summary = await purge_old_archives()
            print()
            print("=== Purge Summary ===")
            for table, count in purge_summary.items():
                print(f"  {table}: {count} rows deleted")
            print("=====================")
    finally:
        await close_db_pool()


if __name__ == "__main__":
    if os.name == "nt":
        policy_cls = getattr(asyncio, "WindowsSelectorEventLoopPolicy", None)
        if policy_cls:
            asyncio.set_event_loop_policy(policy_cls())
    asyncio.run(_main())
