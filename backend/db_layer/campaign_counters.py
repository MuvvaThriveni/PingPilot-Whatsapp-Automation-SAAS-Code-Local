"""Distributed counters for campaign sent/failed counts.

Path: campaign_counters/{campaign_id}/shards/{shard_id}

Each shard holds partial counts. To get totals, sum all shards.
Writers pick a random shard to avoid write contention on a single document.
"""

from database import fetchrow, execute, fetchrow_conn, execute_conn
from observability import log_event


class _CampaignCounters:

    @staticmethod
    async def init_shards(tenant_id: str, campaign_id: str, conn=None):
        """Legacy no-op for Postgres.

        Counters live directly on campaigns table. We keep this method so
        existing call sites can be updated safely.
        """
        try:
            q = """
                UPDATE campaigns
                SET sent_count = 0, failed_count = 0, updated_at = now()
                WHERE tenant_id = %s AND campaign_id = %s::uuid
                """
            if conn is not None:
                await execute_conn(conn, q, tenant_id, campaign_id)
            else:
                await execute(q, tenant_id, campaign_id)
        except Exception as e:
            log_event("db_error", detail=f"campaign_counters.init_shards({campaign_id}) failed: {e}", level="ERROR")

    @staticmethod
    async def increment_sent(tenant_id: str, campaign_id: str, count: int = 1, conn=None):
        """Atomically increment sent_count."""
        try:
            q = """
                UPDATE campaigns
                SET sent_count = sent_count + %s, updated_at = now()
                WHERE tenant_id = %s AND campaign_id = %s::uuid
                """
            args = (int(count), tenant_id, campaign_id)
            if conn is not None:
                await execute_conn(conn, q, *args)
            else:
                await execute(q, *args)
        except Exception as e:
            log_event("db_error", detail=f"campaign_counters.increment_sent({campaign_id}) failed: {e}", level="ERROR")

    @staticmethod
    async def increment_failed(tenant_id: str, campaign_id: str, count: int = 1, conn=None):
        """Atomically increment failed_count."""
        try:
            q = """
                UPDATE campaigns
                SET failed_count = failed_count + %s, updated_at = now()
                WHERE tenant_id = %s AND campaign_id = %s::uuid
                """
            args = (int(count), tenant_id, campaign_id)
            if conn is not None:
                await execute_conn(conn, q, *args)
            else:
                await execute(q, *args)
        except Exception as e:
            log_event("db_error", detail=f"campaign_counters.increment_failed({campaign_id}) failed: {e}", level="ERROR")

    @staticmethod
    async def decrement_sent(tenant_id: str, campaign_id: str, count: int = 1, conn=None):
        """Atomically decrement sent_count (clamped to 0).

        Used when a message is re-queued for retry after a delivery failure.
        The worker will re-increment sent_count if the retry succeeds.
        """
        try:
            q = """
                UPDATE campaigns
                SET sent_count = GREATEST(sent_count - %s, 0), updated_at = now()
                WHERE tenant_id = %s AND campaign_id = %s::uuid
                """
            args = (int(count), tenant_id, campaign_id)
            if conn is not None:
                await execute_conn(conn, q, *args)
            else:
                await execute(q, *args)
        except Exception as e:
            log_event("db_error", detail=f"campaign_counters.decrement_sent({campaign_id}) failed: {e}", level="ERROR")

    @staticmethod
    async def transfer_sent_to_failed(tenant_id: str, campaign_id: str, count: int = 1, conn=None):
        """Atomically move count from sent_count to failed_count.

        Used when the webhook reports a terminal delivery failure for a message
        that the worker had already optimistically counted as 'sent'.
        sent_count is clamped to 0 to avoid negative values.
        """
        try:
            q = """
                UPDATE campaigns
                SET sent_count = GREATEST(sent_count - %s, 0),
                    failed_count = failed_count + %s,
                    updated_at = now()
                WHERE tenant_id = %s AND campaign_id = %s::uuid
                """
            args = (int(count), int(count), tenant_id, campaign_id)
            if conn is not None:
                await execute_conn(conn, q, *args)
            else:
                await execute(q, *args)
        except Exception as e:
            log_event("db_error", detail=f"campaign_counters.transfer_sent_to_failed({campaign_id}) failed: {e}", level="ERROR")

    @staticmethod
    async def get_totals(tenant_id: str, campaign_id: str) -> dict:
        """Read counters from campaigns table."""
        try:
            q = """
                SELECT sent_count, failed_count
                FROM campaigns
                WHERE tenant_id = %s AND campaign_id = %s::uuid
                """
            row = await fetchrow(q, tenant_id, campaign_id)
            if not row:
                return {"sent": 0, "failed": 0}
            return {
                "sent": int(row.get("sent_count") or 0),
                "failed": int(row.get("failed_count") or 0),
            }
        except Exception as e:
            log_event("db_error", detail=f"campaign_counters.get_totals({campaign_id}) failed: {e}", level="ERROR")
            return {"sent": 0, "failed": 0}

    @staticmethod
    async def delete(tenant_id: str, campaign_id: str, conn=None):
        """No-op for Postgres (counters are part of campaigns row).

        Kept for compatibility with legacy call sites.
        """
        try:
            q = """
                UPDATE campaigns
                SET sent_count = 0, failed_count = 0, updated_at = now()
                WHERE tenant_id = %s AND campaign_id = %s::uuid
                """
            if conn is not None:
                await execute_conn(conn, q, tenant_id, campaign_id)
            else:
                await execute(q, tenant_id, campaign_id)
        except Exception as e:
            log_event("db_error", detail=f"campaign_counters.delete({campaign_id}) failed: {e}", level="ERROR")

    @staticmethod
    async def get(tenant_id: str, campaign_id: str) -> dict:
        """Alias for get_totals."""
        return await _CampaignCounters.get_totals(tenant_id, campaign_id)

    @staticmethod
    async def increment(tenant_id: str, campaign_id: str, type: str, count: int = 1, conn=None):
        """Generic increment router."""
        if type == "sent":
            await _CampaignCounters.increment_sent(tenant_id, campaign_id, count, conn=conn)
        elif type == "failed":
            await _CampaignCounters.increment_failed(tenant_id, campaign_id, count, conn=conn)


campaign_counters = _CampaignCounters()
