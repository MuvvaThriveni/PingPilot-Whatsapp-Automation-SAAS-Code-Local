"""Firebase configuration and initialization."""

import os
import firebase_admin
from firebase_admin import credentials

# Initialize Firebase Admin SDK
_firebase_app = None


def init_firebase():
    """Initialize Firebase Admin SDK with service account."""
    global _firebase_app
    
    if _firebase_app is not None:
        return _firebase_app
    
    # Check if Firebase app already exists (e.g., from previous reload)
    try:
        _firebase_app = firebase_admin.get_app()
        print("[INFO] Firebase already initialized, reusing existing app")
        return _firebase_app
    except ValueError:
        # App doesn't exist yet, proceed with initialization
        pass
    
    # Path to service account key file
    service_account_path = os.path.join(
        os.path.dirname(__file__), 
        "firebase-service-account.json"
    )
    
    try:
        if os.path.exists(service_account_path):
            cred = credentials.Certificate(service_account_path)
            _firebase_app = firebase_admin.initialize_app(cred)
        else:
            # Fall back to Application Default Credentials (e.g. GOOGLE_APPLICATION_CREDENTIALS)
            _firebase_app = firebase_admin.initialize_app()
        print("[INFO] Firebase initialized successfully")
        return _firebase_app
    except Exception as e:
        print(f"[ERROR] Failed to initialize Firebase: {e}")
        return None
