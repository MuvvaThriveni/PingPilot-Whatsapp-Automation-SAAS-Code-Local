import datetime
from firebase_config import get_db

class UsersDB:
    def __init__(self):
        self.db = get_db()
        self.collection_name = "users"

    def should_send_trigger(self, phone_number: str) -> bool:
        """Check if we should send a trigger (once every 24 hours)."""
        if not self.db:
            return False
        
        doc_ref = self.db.collection(self.collection_name).document(phone_number)
        doc = doc_ref.get()
        if not doc.exists:
            return True
            
        data = doc.to_dict()
        last_at = data.get("last_trigger_at")
        if not last_at:
            return True
            
        try:
            last_dt = datetime.datetime.fromisoformat(last_at)
            now = datetime.datetime.utcnow()
            diff = now - last_dt
            return diff.total_seconds() > 24 * 3600
        except:
            return True

    def record_trigger(self, phone_number: str):
        """Record the time of the latest trigger."""
        if not self.db:
            return
        
        doc_ref = self.db.collection(self.collection_name).document(phone_number)
        now = datetime.datetime.utcnow().isoformat()
        doc_ref.set({
            "last_trigger_at": now,
            "seen": True
        }, merge=True)

users_db = UsersDB()
