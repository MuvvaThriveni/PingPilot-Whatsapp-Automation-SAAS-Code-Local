"""Pre-loads Firestore data into cache at startup.

Requirement 5: Load all static collections (services, templates, configs, etc.) 
once at application startup and reuse them from cache.
"""

import logging
from firebase_config import get_db
from cache import cache, tenant_key, tenant_by_phone_key, chatbot_config_key, chatbot_rules_key, settings_key
from google.cloud.firestore_v1.base_query import FieldFilter

logger = logging.getLogger(__name__)

def prewarm_cache():
    """Fetches all tenants and their basic configs to warm up the 6h cache."""
    db = get_db()
    if not db:
        logger.warning("[STARTUP] Firestore not available, skipping cache prewarm.")
        return

    logger.info("[STARTUP] Pre-warming Firestore cache...")
    
    try:
        # 1. Warm up Tenants and Phone Mappings
        tenants_ref = db.collection("tenants")
        for doc in tenants_ref.stream():
            tenant_data = doc.to_dict()
            tenant_id = doc.id
            cache.set(tenant_key(tenant_id), tenant_data)
            
            phone_id = tenant_data.get("phone_number_id")
            if phone_id:
                cache.set(tenant_by_phone_key(phone_id), tenant_data)
        
        # 2. Warm up Chatbot Configs
        config_ref = db.collection("chatbot_config")
        for doc in config_ref.stream():
            cache.set(chatbot_config_key(doc.id), doc.to_dict())
            
        # 3. Warm up Chatbot Rules (Active ones)
        rules_ref = db.collection("chatbot_rules")
        # Optimization: group rules by tenant in memory instead of multiple queries
        all_rules = rules_ref.where(filter=FieldFilter("is_active", "==", 1)).stream()
        rules_by_tenant = {}
        for doc in all_rules:
            data = doc.to_dict()
            t_id = data.get("tenant_id")
            if t_id:
                if t_id not in rules_by_tenant:
                    rules_by_tenant[t_id] = []
                rules_by_tenant[t_id].append(data)
        
        for t_id, rules in rules_by_tenant.items():
            cache.set(f"chatbot_rules_active:{t_id}", rules)

        logger.info("[STARTUP] Cache pre-warm complete.")
        
    except Exception as e:
        logger.error(f"[STARTUP] Error during cache pre-warm: {e}")

