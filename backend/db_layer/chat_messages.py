from __future__ import annotations

"""Firestore operations for the `chat_messages` collection (Phase-5: optimized).

Append-only — one document per message (incoming or outgoing chatbot message).
No summary documents, no embedded arrays.
AI conversation context is retrieved via ordered query with LIMIT.

Doc ID: auto-generated.

Optimizations:
- Strict limit enforcement on all queries
- Reduced default limits to minimize document reads
- AI context limited to 6 messages (from 10) to reduce reads
"""

import datetime
from google.cloud.firestore_v1.base_query import FieldFilter
from firebase_config import get_db


def _col():
    db = get_db()
    return db.collection("chat_messages") if db else None


class _ChatMessages:

    @staticmethod
    def add(tenant_id: str, data: dict):
        """Append a single chat message document."""
        col = _col()
        if not col:
            return
        try:
            # Store only required fields — minimize document size
            doc = {
                "tenant_id": tenant_id,
                "contact_phone": data.get("contact_phone", ""),
                "contact_name": data.get("contact_name", ""),
                "message_text": data.get("message_text", ""),
                "direction": data.get("direction", ""),
                "created_at": data.get("created_at", datetime.datetime.utcnow().isoformat()),
            }
            col.add(doc)
        except Exception as e:
            print(f"[db_layer.chat_messages] add failed: {e}")

    @staticmethod
    def get_recent(tenant_id: str, contact_phone: str, limit: int = 6) -> list[dict]:
        """Get the N most recent messages for a contact, ordered oldest-first.

        Used to build AI conversation context.
        Default reduced from 10 to 6 to minimize reads per webhook.
        """
        limit = max(1, min(limit, 20))  # Hard cap at 20
        col = _col()
        if not col:
            return []
        try:
            docs = (
                col.where(filter=FieldFilter("tenant_id", "==", tenant_id))
                .where(filter=FieldFilter("contact_phone", "==", contact_phone))
                .order_by("created_at", direction="DESCENDING")
                .limit(limit)
                .stream()
            )
            results = [doc.to_dict() for doc in docs]
            results.reverse()  # oldest first for AI context
            return results
        except Exception as e:
            print(f"[db_layer.chat_messages] get_recent failed: {e}")
            return []

    @staticmethod
    def get_conversation_list(tenant_id: str, limit: int = 50, cursor: str = None) -> tuple[list[dict], str | None]:
        """Get the most recent chat messages across all contacts.

        Returns (docs, next_cursor). Caller deduplicates by contact_phone.
        limit clamped to [1, 200] (reduced from 500).
        """
        limit = max(1, min(limit, 200))
        col = _col()
        if not col:
            return [], None
        try:
            query = (
                col.where(filter=FieldFilter("tenant_id", "==", tenant_id))
                .order_by("created_at", direction="DESCENDING")
            )
            if cursor:
                query = query.start_after({"created_at": cursor})
            raw = list(query.limit(limit + 1).stream())
            has_next = len(raw) > limit
            docs = [doc.to_dict() for doc in raw[:limit]]
            next_cursor = docs[-1]["created_at"] if has_next and docs else None
            return docs, next_cursor
        except Exception as e:
            print(f"[db_layer.chat_messages] get_conversation_list failed: {e}")
            return [], None

    @staticmethod
    def get_user_messages(tenant_id: str, contact_phone: str, limit: int = 50,
                          cursor: str = None) -> tuple[list[dict], str | None]:
        """Get messages for a specific contact, ordered oldest-first.

        Returns (docs, next_cursor). limit clamped to [1, 100].
        """
        limit = max(1, min(limit, 100))
        col = _col()
        if not col:
            return [], None
        try:
            query = (
                col.where(filter=FieldFilter("tenant_id", "==", tenant_id))
                .where(filter=FieldFilter("contact_phone", "==", contact_phone))
                .order_by("created_at", direction="ASCENDING")
            )
            if cursor:
                query = query.start_after({"created_at": cursor})
            raw = list(query.limit(limit + 1).stream())
            has_next = len(raw) > limit
            docs = [doc.to_dict() for doc in raw[:limit]]
            next_cursor = docs[-1]["created_at"] if has_next and docs else None
            return docs, next_cursor
        except Exception as e:
            print(f"[db_layer.chat_messages] get_user_messages failed: {e}")
            return [], None

    @staticmethod
    def build_ai_context(tenant_id: str, contact_phone: str, limit: int = 6) -> list[dict]:
        """Build OpenAI-compatible message history from recent chat messages.

        Returns list of {"role": "user"|"assistant", "content": "..."}.
        Default reduced from 10 to 6 for cost + quota savings.
        """
        recent = _ChatMessages.get_recent(tenant_id, contact_phone, limit)
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
