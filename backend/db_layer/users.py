import datetime
from cache import cache

class UsersDB:
    """Manages user state in memory (Requirement 7).
    
    Resets on server restart, which is acceptable for 'in-memory session state'
    and results in zero Firestore reads for repeated triggers (Requirement 10).
    """

    def should_send_trigger(self, phone_number: str) -> bool:
        """Check if we should send a trigger (once every 24 hours). Cached in memory."""
        last_at = cache.get_session(f"user_trigger:{phone_number}")
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
        """Record the time of the latest trigger in memory."""
        now = datetime.datetime.utcnow().isoformat()
        cache.set_session(f"user_trigger:{phone_number}", now)

users_db = UsersDB()
