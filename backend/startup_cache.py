"""Pre-loads database data into cache at startup.

Requirement 5: Load all static tables (tenants, configs, rules, etc.) once at
application startup and reuse them from cache.
"""

import logging
from database import fetch
from cache import cache, tenant_key, tenant_by_phone_key, chatbot_config_key, chatbot_rules_key, settings_key

logger = logging.getLogger(__name__)

async def prewarm_cache():
    """Fetches all tenants and their basic configs to warm up the 6h cache."""
    logger.info("[STARTUP] Pre-warming cache...")
    
    try:
        # 1. Warm up Tenants and Phone Mappings
        tenants_rows = await fetch("SELECT * FROM tenants")
        for r in tenants_rows:
            tenant_data = dict(r)
            tenant_id = tenant_data.get("tenant_id", "")
            if not tenant_id:
                continue
            cache.set(tenant_key(tenant_id), tenant_data)

            phone_id = tenant_data.get("phone_number_id")
            if phone_id:
                cache.set(tenant_by_phone_key(phone_id), tenant_data)

        # 2. Warm up Chatbot Configs
        cfg_rows = await fetch("SELECT * FROM chatbot_config")
        for r in cfg_rows:
            d = dict(r)
            t_id = d.get("tenant_id")
            if t_id:
                cache.set(chatbot_config_key(t_id), d)

        # 3. Warm up Chatbot Rules (Active ones)
        rules_rows = await fetch(
            "SELECT * FROM chatbot_rules WHERE is_active = TRUE ORDER BY priority DESC, id DESC"
        )
        rules_by_tenant: dict[str, list] = {}
        for r in rules_rows:
            d = dict(r)
            t_id = d.get("tenant_id")
            if not t_id:
                continue
            if t_id not in rules_by_tenant:
                rules_by_tenant[t_id] = []
            rules_by_tenant[t_id].append(d)

        for t_id, rules in rules_by_tenant.items():
            cache.set(f"chatbot_rules_active:{t_id}", rules)

        logger.info("[STARTUP] Cache pre-warm complete.")
        
    except Exception as e:
        logger.error(f"[STARTUP] Error during cache pre-warm: {e}")

