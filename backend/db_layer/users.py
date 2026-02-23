from firebase_config import get_db

class UsersDB:
    def __init__(self):
        self.db = get_db()
        self.collection_name = "users"

    def is_user_seen(self, phone_number: str) -> bool:
        """Check if the user has been seen before."""
        if not self.db:
            return False
        
        doc_ref = self.db.collection(self.collection_name).document(phone_number)
        doc = doc_ref.get()
        if doc.exists:
            return doc.to_dict().get("seen", False)
        return False

    def mark_user_seen(self, phone_number: str):
        """Mark a user as seen in Firestore."""
        if not self.db:
            return
        
        doc_ref = self.db.collection(self.collection_name).document(phone_number)
        doc_ref.set({"seen": True}, merge=True)

users_db = UsersDB()
