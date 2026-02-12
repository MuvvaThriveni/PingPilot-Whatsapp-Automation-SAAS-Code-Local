"""Firebase configuration and initialization."""

import os
import firebase_admin
from firebase_admin import credentials, firestore

# Initialize Firebase Admin SDK
_firebase_app = None
_db = None


def init_firebase():
    """Initialize Firebase Admin SDK with service account."""
    global _firebase_app, _db
    
    if _firebase_app is not None:
        return _db
    
    # Path to service account key file
    service_account_path = os.path.join(
        os.path.dirname(__file__), 
        "firebase-service-account.json"
    )
    
    if not os.path.exists(service_account_path):
        print("[WARN] Firebase service account file not found. Using local storage.")
        return None
    
    try:
        cred = credentials.Certificate(service_account_path)
        _firebase_app = firebase_admin.initialize_app(cred)
        _db = firestore.client()
        print("[INFO] Firebase initialized successfully")
        return _db
    except Exception as e:
        print(f"[ERROR] Failed to initialize Firebase: {e}")
        return None


def get_db():
    """Get Firestore database client."""
    global _db
    if _db is None:
        init_firebase()
    return _db
