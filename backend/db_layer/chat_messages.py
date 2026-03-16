from __future__ import annotations

"""Postgres operations for the `chat_messages` table (Phase-5: optimized).

Append-only — one document per message (incoming or outgoing chatbot message).
No summary documents, no embedded arrays.
AI conversation context is retrieved via ordered query with LIMIT.

Primary key: (tenant_id, id).

Optimizations:
- Strict limit enforcement on all queries
- Reduced default limits to minimize document reads
- AI context limited to 6 messages (from 10) to reduce reads
"""

import datetime
from database import fetchrow, fetch
from observability import log_event
from utils.time_utils import get_ist_now_iso


def _ist_now_iso() -> str:
    return get_ist_now_iso()


class _ChatMessages:

    @staticmethod
    async def add(tenant_id: str, data: dict):
        """Append a single chat message document."""
        try:
            created_at = data.get("created_at")
            await fetchrow(
                """
                INSERT INTO chat_messages (
                    tenant_id,
                    contact_phone,
                    contact_name,
                    message_text,
                    direction,
                    created_at
                )
                VALUES (%s,%s,%s,%s,%s, COALESCE(%s::timestamptz, now()))
                RETURNING id
                """,
                tenant_id,
                data.get("contact_phone", ""),
                data.get("contact_name", ""),
                data.get("message_text", ""),
                data.get("direction", ""),
                created_at if created_at else None,
            )
        except Exception as e:
            log_event("db_error", detail=f"chat_messages.add failed: {e}", level="ERROR")

    @staticmethod
    async def get_recent(tenant_id: str, contact_phone: str, limit: int = 6) -> list[dict]:
        """Get the N most recent messages for a contact, ordered oldest-first.

        Used to build AI conversation context.
        Default reduced from 10 to 6 to minimize reads per webhook.
        """
        limit = max(1, min(limit, 20))  # Hard cap at 20
        try:
            rows = await fetch(
                """
                SELECT tenant_id, contact_phone, contact_name, message_text, direction, created_at
                FROM chat_messages
                WHERE tenant_id = %s AND contact_phone = %s
                ORDER BY created_at DESC, id DESC
                LIMIT %s
                """,
                tenant_id,
                contact_phone,
                limit,
            )
            results = [dict(r) for r in rows]
            results.reverse()  # oldest first for AI context
            return results
        except Exception as e:
            log_event("db_error", detail=f"chat_messages.get_recent failed: {e}", level="ERROR")
            return []

    @staticmethod
    async def get_conversation_list(tenant_id: str, limit: int = 50, cursor: str = None) -> tuple[list[dict], str | None]:
        """Get the most recent chat messages across all contacts.

        Returns (docs, next_cursor). Caller deduplicates by contact_phone.
        limit clamped to [1, 200] (reduced from 500).
        """
        limit = max(1, min(limit, 200))
        try:
            where = ["tenant_id = %s"]
            args: list = [tenant_id]

            cursor_created_at: str | None = None
            cursor_id: int | None = None
            if cursor and "::" in cursor:
                parts = cursor.split("::", 1)
                cursor_created_at = parts[0]
                try:
                    cursor_id = int(parts[1])
                except ValueError:
                    cursor_id = None
            elif cursor:
                cursor_created_at = cursor

            if cursor_created_at and cursor_id is not None:
                args.append(cursor_created_at)
                args.append(cursor_id)
                where.append("(created_at, id) < (%s::timestamptz, %s)")
            elif cursor_created_at:
                args.append(cursor_created_at)
                where.append("created_at < %s::timestamptz")

            q = (
                "SELECT id, tenant_id, contact_phone, contact_name, message_text, direction, created_at "
                "FROM chat_messages WHERE "
                + " AND ".join(where)
                + " ORDER BY created_at DESC, id DESC LIMIT "
                + str(limit + 1)
            )
            raw = await fetch(q, *args)
            has_next = len(raw) > limit
            page = raw[:limit]
            docs = [dict(r) for r in page]
            next_cursor = None
            if has_next and page:
                last = page[-1]
                next_cursor = f"{last['created_at'].isoformat()}::{last['id']}"
            return docs, next_cursor
        except Exception as e:
            log_event("db_error", detail=f"chat_messages.get_conversation_list failed: {e}", level="ERROR")
            return [], None

    @staticmethod
    async def get_user_messages(tenant_id: str, contact_phone: str, limit: int = 50,
                                cursor: str = None) -> tuple[list[dict], str | None]:
        """Get messages for a specific contact, ordered oldest-first.

        Returns (docs, next_cursor). limit clamped to [1, 100].
        """
        limit = max(1, min(limit, 100))
        try:
            where = ["tenant_id = %s", "contact_phone = %s"]
            args: list = [tenant_id, contact_phone]

            cursor_created_at: str | None = None
            cursor_id: int | None = None
            if cursor and "::" in cursor:
                parts = cursor.split("::", 1)
                cursor_created_at = parts[0]
                try:
                    cursor_id = int(parts[1])
                except ValueError:
                    cursor_id = None
            elif cursor:
                cursor_created_at = cursor

            if cursor_created_at and cursor_id is not None:
                args.append(cursor_created_at)
                args.append(cursor_id)
                where.append("(created_at, id) > (%s::timestamptz, %s)")
            elif cursor_created_at:
                args.append(cursor_created_at)
                where.append("created_at > %s::timestamptz")

            q = (
                "SELECT id, tenant_id, contact_phone, contact_name, message_text, direction, created_at "
                "FROM chat_messages WHERE "
                + " AND ".join(where)
                + " ORDER BY created_at ASC, id ASC LIMIT "
                + str(limit + 1)
            )
            raw = await fetch(q, *args)
            has_next = len(raw) > limit
            page = raw[:limit]
            docs = [dict(r) for r in page]
            next_cursor = None
            if has_next and page:
                last = page[-1]
                next_cursor = f"{last['created_at'].isoformat()}::{last['id']}"
            return docs, next_cursor
        except Exception as e:
            log_event("db_error", detail=f"chat_messages.get_user_messages failed: {e}", level="ERROR")
            return [], None

    @staticmethod
    async def build_ai_context(tenant_id: str, contact_phone: str, limit: int = 6) -> list[dict]:
        """Build OpenAI-compatible message history from recent chat messages.

        Returns list of {"role": "user"|"assistant", "content": "..."}.
        Default reduced from 10 to 6 for cost + quota savings.
        """
        recent = await _ChatMessages.get_recent(tenant_id, contact_phone, limit)
        context = []
        for msg in recent:
            direction = msg.get("direction", "")
            text = msg.get("message_text", "")
            if not text:
                continue
            if direction == "incoming":
                context.append({"role": "user", "content": text})
            elif direction == "outgoing":
                context.append({"role": "assistant", "content": text})
        return context


chat_messages = _ChatMessages()
