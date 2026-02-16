"""Firestore data access layer for WappFlow.

All Firestore reads/writes go through this module.
Uses the existing firebase_config.get_db() client — does NOT reinitialize Firebase.
"""

from db_layer.tenants import tenants
from db_layer.chatbot import chatbot_config, chatbot_rules
from db_layer.chat_messages import chat_messages
from db_layer.messages import messages
from db_layer.campaigns import campaigns
from db_layer.campaign_recipients import campaign_recipients
from db_layer.campaign_counters import campaign_counters
from db_layer.webhook_events import webhook_events
from db_layer.usage_events import usage_events
from db_layer.template_cache import template_cache_db
from db_layer.secrets import secrets

__all__ = [
    "tenants",
    "chatbot_config",
    "chatbot_rules",
    "chat_messages",
    "messages",
    "campaigns",
    "campaign_recipients",
    "campaign_counters",
    "webhook_events",
    "usage_events",
    "template_cache_db",
    "secrets",
]
