CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE IF NOT EXISTS tenants (
  tenant_id TEXT PRIMARY KEY,
  business_account_id TEXT NOT NULL DEFAULT '',
  phone_number_id TEXT NOT NULL DEFAULT '',
  access_token TEXT NOT NULL DEFAULT '',
  token_ref TEXT NOT NULL DEFAULT '',
  webhook_verify_token TEXT NOT NULL DEFAULT '',
  meta_app_secret TEXT NOT NULL DEFAULT '',
  is_configured BOOLEAN NOT NULL DEFAULT FALSE,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_tenants_phone_number_id
  ON tenants (phone_number_id)
  WHERE phone_number_id <> '';

CREATE INDEX IF NOT EXISTS idx_tenants_updated_at
  ON tenants (updated_at DESC);


CREATE TABLE IF NOT EXISTS chatbot_config (
  tenant_id TEXT PRIMARY KEY REFERENCES tenants(tenant_id) ON DELETE CASCADE,
  is_enabled BOOLEAN NOT NULL DEFAULT FALSE,
  fallback_message TEXT NOT NULL DEFAULT '',
  use_ai BOOLEAN NOT NULL DEFAULT FALSE,
  button_text_mappings JSONB,
  button_id_mappings JSONB,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);


CREATE TABLE IF NOT EXISTS chatbot_rules (
  id BIGSERIAL PRIMARY KEY,
  tenant_id TEXT NOT NULL REFERENCES tenants(tenant_id) ON DELETE CASCADE,
  keyword TEXT NOT NULL,
  response TEXT NOT NULL,
  priority INTEGER NOT NULL DEFAULT 0,
  is_active BOOLEAN NOT NULL DEFAULT TRUE,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_chatbot_rules_tenant_priority
  ON chatbot_rules (tenant_id, priority DESC);

CREATE INDEX IF NOT EXISTS idx_chatbot_rules_tenant_active_priority
  ON chatbot_rules (tenant_id, is_active, priority DESC);


CREATE TABLE IF NOT EXISTS campaigns (
  tenant_id TEXT NOT NULL REFERENCES tenants(tenant_id) ON DELETE CASCADE,
  campaign_id UUID NOT NULL,
  name TEXT NOT NULL DEFAULT '',
  template_name TEXT NOT NULL DEFAULT '',
  header_image_url TEXT NOT NULL DEFAULT '',
  total_contacts INTEGER NOT NULL DEFAULT 0,
  status TEXT NOT NULL DEFAULT '',
  delay_ms INTEGER NOT NULL DEFAULT 1000,
  scheduled_at TIMESTAMPTZ,
  last_processed_index INTEGER,
  worker_heartbeat TIMESTAMPTZ,
  error_message TEXT NOT NULL DEFAULT '',
  sent_count INTEGER NOT NULL DEFAULT 0,
  failed_count INTEGER NOT NULL DEFAULT 0,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (tenant_id, campaign_id)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_campaigns_campaign_id
  ON campaigns (campaign_id);

CREATE INDEX IF NOT EXISTS idx_campaigns_tenant_created_at
  ON campaigns (tenant_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_campaigns_status_scheduled_at
  ON campaigns (status, scheduled_at);

CREATE INDEX IF NOT EXISTS idx_campaigns_status
  ON campaigns (status);


CREATE TABLE IF NOT EXISTS campaign_recipients (
  tenant_id TEXT NOT NULL REFERENCES tenants(tenant_id) ON DELETE CASCADE,
  campaign_id UUID NOT NULL,
  contact_phone TEXT NOT NULL,
  contact_name TEXT NOT NULL DEFAULT '',
  contact_data JSONB NOT NULL DEFAULT '{}'::jsonb,
  status TEXT NOT NULL DEFAULT 'pending',
  wa_message_id TEXT NOT NULL DEFAULT '',
  error_message TEXT NOT NULL DEFAULT '',
  attempt_count INTEGER NOT NULL DEFAULT 0,
  recipient_index INTEGER NOT NULL DEFAULT 0,
  last_attempt_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ,
  PRIMARY KEY (tenant_id, campaign_id, contact_phone),
  FOREIGN KEY (tenant_id, campaign_id) REFERENCES campaigns(tenant_id, campaign_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_campaign_recipients_pending
  ON campaign_recipients (tenant_id, campaign_id, status, recipient_index);

CREATE INDEX IF NOT EXISTS idx_campaign_recipients_campaign_index
  ON campaign_recipients (tenant_id, campaign_id, recipient_index);

CREATE INDEX IF NOT EXISTS idx_campaign_recipients_campaign_status
  ON campaign_recipients (tenant_id, campaign_id, status);


CREATE TABLE IF NOT EXISTS messages (
  id BIGSERIAL PRIMARY KEY,
  tenant_id TEXT NOT NULL REFERENCES tenants(tenant_id) ON DELETE CASCADE,
  direction TEXT NOT NULL DEFAULT '',
  product_type TEXT NOT NULL DEFAULT '',
  contact_phone TEXT NOT NULL DEFAULT '',
  contact_name TEXT NOT NULL DEFAULT '',
  message_text TEXT NOT NULL DEFAULT '',
  message_type TEXT NOT NULL DEFAULT 'text',
  wa_message_id TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT '',
  template_name TEXT NOT NULL DEFAULT '',
  campaign_id UUID,
  media_id TEXT NOT NULL DEFAULT '',
  error_message TEXT NOT NULL DEFAULT '',
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_messages_tenant_created_at
  ON messages (tenant_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_messages_tenant_product_created_at
  ON messages (tenant_id, product_type, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_messages_tenant_status_created_at
  ON messages (tenant_id, status, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_messages_tenant_campaign_created_at
  ON messages (tenant_id, campaign_id, created_at ASC);

CREATE UNIQUE INDEX IF NOT EXISTS idx_messages_tenant_wa_message_id
  ON messages (tenant_id, wa_message_id)
  WHERE wa_message_id <> '';

CREATE INDEX IF NOT EXISTS idx_messages_created_at
  ON messages (created_at ASC);


CREATE TABLE IF NOT EXISTS chat_messages (
  id BIGSERIAL PRIMARY KEY,
  tenant_id TEXT NOT NULL REFERENCES tenants(tenant_id) ON DELETE CASCADE,
  contact_phone TEXT NOT NULL,
  contact_name TEXT NOT NULL DEFAULT '',
  message_text TEXT NOT NULL DEFAULT '',
  direction TEXT NOT NULL DEFAULT '',
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_chat_messages_tenant_created_at
  ON chat_messages (tenant_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_chat_messages_tenant_contact_created_desc
  ON chat_messages (tenant_id, contact_phone, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_chat_messages_tenant_contact_created_asc
  ON chat_messages (tenant_id, contact_phone, created_at ASC);


CREATE TABLE IF NOT EXISTS webhook_events (
  tenant_id TEXT NOT NULL REFERENCES tenants(tenant_id) ON DELETE CASCADE,
  event_id TEXT NOT NULL,
  event_type TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'received',
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  processed_at TIMESTAMPTZ,
  PRIMARY KEY (tenant_id, event_id)
);

CREATE INDEX IF NOT EXISTS idx_webhook_events_tenant_status_created
  ON webhook_events (tenant_id, status, created_at ASC);


CREATE TABLE IF NOT EXISTS usage_events (
  id BIGSERIAL PRIMARY KEY,
  tenant_id TEXT NOT NULL REFERENCES tenants(tenant_id) ON DELETE CASCADE,
  event_type TEXT NOT NULL,
  product_type TEXT NOT NULL DEFAULT '',
  campaign_id UUID,
  contact_phone TEXT NOT NULL DEFAULT '',
  billable BOOLEAN NOT NULL DEFAULT TRUE,
  month_key TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_usage_events_tenant_month
  ON usage_events (tenant_id, month_key);

CREATE INDEX IF NOT EXISTS idx_usage_events_tenant_created_at
  ON usage_events (tenant_id, created_at DESC);


CREATE TABLE IF NOT EXISTS template_cache (
  doc_id TEXT PRIMARY KEY,
  tenant_id TEXT NOT NULL REFERENCES tenants(tenant_id) ON DELETE CASCADE,
  template_name TEXT NOT NULL DEFAULT '',
  language TEXT NOT NULL DEFAULT 'en_US',
  status TEXT NOT NULL DEFAULT '',
  components JSONB NOT NULL DEFAULT '[]'::jsonb,
  param_count INTEGER NOT NULL DEFAULT 0,
  fetched_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_template_cache_tenant_status
  ON template_cache (tenant_id, status);


CREATE TABLE IF NOT EXISTS user_triggers (
  tenant_id TEXT NOT NULL REFERENCES tenants(tenant_id) ON DELETE CASCADE,
  phone_number TEXT NOT NULL,
  last_trigger_at TIMESTAMPTZ NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (tenant_id, phone_number)
);

CREATE INDEX IF NOT EXISTS idx_user_triggers_tenant_updated_at
  ON user_triggers (tenant_id, updated_at DESC);
