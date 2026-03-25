-- ============================================================================
-- Retention Phase 1: Archive tables + daily aggregation table
-- Safe to run multiple times (all IF NOT EXISTS).
-- No partitioning. No data movement. Schema-only.
-- ============================================================================

-- ── messages_archive ────────────────────────────────────────────────────────
-- Mirror of `messages` + archived_at. No FK to tenants (archived rows must
-- survive even if the tenant record is later purged).

CREATE TABLE IF NOT EXISTS messages_archive (
  id BIGINT NOT NULL,
  tenant_id TEXT NOT NULL,
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
  created_at TIMESTAMPTZ NOT NULL,
  updated_at TIMESTAMPTZ,
  archived_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (id)
);

CREATE INDEX IF NOT EXISTS idx_messages_archive_tenant_created
  ON messages_archive (tenant_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_messages_archive_tenant_wa_message_id
  ON messages_archive (tenant_id, wa_message_id)
  WHERE wa_message_id <> '';

CREATE INDEX IF NOT EXISTS idx_messages_archive_tenant_campaign
  ON messages_archive (tenant_id, campaign_id)
  WHERE campaign_id IS NOT NULL;


-- ── chat_messages_archive ───────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS chat_messages_archive (
  id BIGINT NOT NULL,
  tenant_id TEXT NOT NULL,
  contact_phone TEXT NOT NULL,
  contact_name TEXT NOT NULL DEFAULT '',
  message_text TEXT NOT NULL DEFAULT '',
  direction TEXT NOT NULL DEFAULT '',
  created_at TIMESTAMPTZ NOT NULL,
  archived_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (id)
);

CREATE INDEX IF NOT EXISTS idx_chat_messages_archive_tenant_created
  ON chat_messages_archive (tenant_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_chat_messages_archive_tenant_contact_created
  ON chat_messages_archive (tenant_id, contact_phone, created_at DESC);


-- ── webhook_events_archive ──────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS webhook_events_archive (
  tenant_id TEXT NOT NULL,
  event_id TEXT NOT NULL,
  event_type TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'received',
  created_at TIMESTAMPTZ NOT NULL,
  processed_at TIMESTAMPTZ,
  archived_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (tenant_id, event_id)
);

CREATE INDEX IF NOT EXISTS idx_webhook_events_archive_tenant_created
  ON webhook_events_archive (tenant_id, created_at DESC);


-- ── usage_events_archive ────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS usage_events_archive (
  id BIGINT NOT NULL,
  tenant_id TEXT NOT NULL,
  event_type TEXT NOT NULL,
  product_type TEXT NOT NULL DEFAULT '',
  campaign_id UUID,
  contact_phone TEXT NOT NULL DEFAULT '',
  billable BOOLEAN NOT NULL DEFAULT TRUE,
  month_key TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL,
  archived_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (id)
);

CREATE INDEX IF NOT EXISTS idx_usage_events_archive_tenant_month
  ON usage_events_archive (tenant_id, month_key);

CREATE INDEX IF NOT EXISTS idx_usage_events_archive_tenant_created
  ON usage_events_archive (tenant_id, created_at DESC);


-- ── daily_message_stats (aggregation table) ─────────────────────────────────
-- Pre-aggregated daily counts per tenant/product/direction/status.
-- Populated BEFORE archiving so monthly usage dashboards stay accurate
-- even after live rows are moved to archive.

CREATE TABLE IF NOT EXISTS daily_message_stats (
  tenant_id TEXT NOT NULL,
  stat_date DATE NOT NULL,
  product_type TEXT NOT NULL DEFAULT '',
  direction TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT '',
  message_count INTEGER NOT NULL DEFAULT 0,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (tenant_id, stat_date, product_type, direction, status)
);

CREATE INDEX IF NOT EXISTS idx_daily_message_stats_tenant_date
  ON daily_message_stats (tenant_id, stat_date DESC);
