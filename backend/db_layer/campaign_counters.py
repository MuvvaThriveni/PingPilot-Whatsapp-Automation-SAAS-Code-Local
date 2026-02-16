"""Distributed counters for campaign sent/failed counts.

Path: campaign_counters/{campaign_id}/shards/{shard_id}

Each shard holds partial counts. To get totals, sum all shards.
Writers pick a random shard to avoid write contention on a single document.
"""

import random
from google.cloud.firestore_v1 import Increment
from firebase_config import get_db

NUM_SHARDS = 10


def _shards_col(campaign_id: str):
    db = get_db()
    if not db:
        return None
    return db.collection("campaign_counters").document(campaign_id).collection("shards")


class _CampaignCounters:

    @staticmethod
    def init_shards(campaign_id: str):
        """Create all shard documents with zero counts. Call once when campaign is created."""
        col = _shards_col(campaign_id)
        if not col:
            return
        db = get_db()
        try:
            batch = db.batch()
            for i in range(NUM_SHARDS):
                ref = col.document(str(i))
                batch.set(ref, {"sent": 0, "failed": 0})
            batch.commit()
        except Exception as e:
            print(f"[db_layer.campaign_counters] init_shards({campaign_id}) failed: {e}")

    @staticmethod
    def increment_sent(campaign_id: str, count: int = 1):
        """Atomically increment sent count on a random shard."""
        col = _shards_col(campaign_id)
        if not col:
            return
        try:
            shard_id = str(random.randint(0, NUM_SHARDS - 1))
            col.document(shard_id).update({
                "sent": Increment(count)
            })
        except Exception as e:
            print(f"[db_layer.campaign_counters] increment_sent({campaign_id}) failed: {e}")

    @staticmethod
    def increment_failed(campaign_id: str, count: int = 1):
        """Atomically increment failed count on a random shard."""
        col = _shards_col(campaign_id)
        if not col:
            return
        try:
            shard_id = str(random.randint(0, NUM_SHARDS - 1))
            col.document(shard_id).update({
                "failed": Increment(count)
            })
        except Exception as e:
            print(f"[db_layer.campaign_counters] increment_failed({campaign_id}) failed: {e}")

    @staticmethod
    def get_totals(campaign_id: str) -> dict:
        """Sum all shards to get total sent/failed counts."""
        col = _shards_col(campaign_id)
        if not col:
            return {"sent": 0, "failed": 0}
        try:
            total_sent = 0
            total_failed = 0
            for doc in col.stream():
                d = doc.to_dict()
                total_sent += d.get("sent", 0)
                total_failed += d.get("failed", 0)
            return {"sent": total_sent, "failed": total_failed}
        except Exception as e:
            print(f"[db_layer.campaign_counters] get_totals({campaign_id}) failed: {e}")
            return {"sent": 0, "failed": 0}

    @staticmethod
    def delete(campaign_id: str):
        """Delete all shards for a campaign."""
        col = _shards_col(campaign_id)
        if not col:
            return
        db = get_db()
        try:
            batch = db.batch()
            for doc in col.stream():
                batch.delete(doc.reference)
            batch.commit()
            # Delete the parent document
            db.collection("campaign_counters").document(campaign_id).delete()
        except Exception as e:
            print(f"[db_layer.campaign_counters] delete({campaign_id}) failed: {e}")


campaign_counters = _CampaignCounters()
