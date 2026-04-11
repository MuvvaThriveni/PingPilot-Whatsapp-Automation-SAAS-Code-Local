-- ═══════════════════════════════════════════════════════════════════
-- MIGRATION: Chatbot System Redesign (Phase-8)
-- Run AFTER existing schema.sql
--
-- Changes:
--   1. chatbot_config: add fallback_template_name, fallback_cooldown_hours
--   2. chatbot_rules: add response_type, match_type
--   3. NEW TABLE: chatbot_button_mappings (replaces hardcoded maps)
--   4. NEW TABLE: chatbot_flows (future flow builder)
--   5. DATA MIGRATION: preserve existing tenant mappings
-- ═══════════════════════════════════════════════════════════════════

-- ── 1. Extend chatbot_config with fallback template settings ─────
ALTER TABLE chatbot_config
  ADD COLUMN IF NOT EXISTS fallback_template_name TEXT NOT NULL DEFAULT '';

ALTER TABLE chatbot_config
  ADD COLUMN IF NOT EXISTS fallback_cooldown_hours INTEGER NOT NULL DEFAULT 24;

COMMENT ON COLUMN chatbot_config.fallback_template_name IS
  'Template name to send as fallback when no rule matches. Empty = no fallback.';
COMMENT ON COLUMN chatbot_config.fallback_cooldown_hours IS
  'Hours between fallback trigger sends per contact. Default 24.';


-- ── 2. Extend chatbot_rules with response_type + match_type ──────
ALTER TABLE chatbot_rules
  ADD COLUMN IF NOT EXISTS response_type TEXT NOT NULL DEFAULT 'text';

ALTER TABLE chatbot_rules
  ADD COLUMN IF NOT EXISTS match_type TEXT NOT NULL DEFAULT 'contains';

-- Add CHECK constraints (safe with IF NOT EXISTS pattern)
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'chk_response_type'
  ) THEN
    ALTER TABLE chatbot_rules
      ADD CONSTRAINT chk_response_type
      CHECK (response_type IN ('text', 'template'));
  END IF;
END$$;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'chk_match_type'
  ) THEN
    ALTER TABLE chatbot_rules
      ADD CONSTRAINT chk_match_type
      CHECK (match_type IN ('exact', 'contains', 'starts_with'));
  END IF;
END$$;


-- ── 3. New table: chatbot_button_mappings ─────────────────────────
CREATE TABLE IF NOT EXISTS chatbot_button_mappings (
  id BIGSERIAL PRIMARY KEY,
  tenant_id TEXT NOT NULL REFERENCES tenants(tenant_id) ON DELETE CASCADE,

  -- What to match
  button_text TEXT NOT NULL DEFAULT '',
  button_id TEXT NOT NULL DEFAULT '',

  -- What to respond with
  template_name TEXT NOT NULL,

  -- Metadata
  is_active BOOLEAN NOT NULL DEFAULT TRUE,
  priority INTEGER NOT NULL DEFAULT 0,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),

  -- At least one of button_text or button_id must be set
  CONSTRAINT chk_button_mapping_has_trigger
    CHECK (button_text <> '' OR button_id <> '')
);

-- Performance indexes for webhook lookup
CREATE INDEX IF NOT EXISTS idx_button_mappings_tenant_active_text
  ON chatbot_button_mappings (tenant_id, button_text)
  WHERE is_active = TRUE AND button_text <> '';

CREATE INDEX IF NOT EXISTS idx_button_mappings_tenant_active_id
  ON chatbot_button_mappings (tenant_id, button_id)
  WHERE is_active = TRUE AND button_id <> '';

-- Prevent duplicate mappings per tenant
CREATE UNIQUE INDEX IF NOT EXISTS idx_button_mappings_unique_text
  ON chatbot_button_mappings (tenant_id, button_text)
  WHERE button_text <> '';

CREATE UNIQUE INDEX IF NOT EXISTS idx_button_mappings_unique_id
  ON chatbot_button_mappings (tenant_id, button_id)
  WHERE button_id <> '';


-- ── 4. chatbot_flows (future-ready: visual flow builder) ─────────
CREATE TABLE IF NOT EXISTS chatbot_flows (
  id BIGSERIAL PRIMARY KEY,
  tenant_id TEXT NOT NULL REFERENCES tenants(tenant_id) ON DELETE CASCADE,
  name TEXT NOT NULL DEFAULT 'Untitled Flow',
  description TEXT NOT NULL DEFAULT '',

  -- The flow definition (nodes + edges)
  flow_data JSONB NOT NULL DEFAULT '{
    "nodes": [],
    "edges": [],
    "variables": {}
  }'::jsonb,

  -- Trigger configuration
  trigger_type TEXT NOT NULL DEFAULT 'keyword'
    CHECK (trigger_type IN ('keyword', 'button', 'first_message', 'manual')),
  trigger_value TEXT NOT NULL DEFAULT '',

  is_active BOOLEAN NOT NULL DEFAULT FALSE,
  version INTEGER NOT NULL DEFAULT 1,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_flows_tenant_active
  ON chatbot_flows (tenant_id, is_active)
  WHERE is_active = TRUE;

CREATE INDEX IF NOT EXISTS idx_flows_tenant_trigger
  ON chatbot_flows (tenant_id, trigger_type, trigger_value)
  WHERE is_active = TRUE;


-- ── 5. Data Migration: preserve existing tenant mappings ─────────
-- Migrate button mappings from chatbot_config JSONB into the new table
-- for any tenant that had custom button_text_mappings stored
-- (Skip if chatbot_button_mappings already has data)

DO $$
DECLARE
  _tenant RECORD;
  _key TEXT;
  _val TEXT;
  _cols_exist BOOLEAN;
BEGIN
  -- Check if the legacy JSONB columns exist before attempting migration
  SELECT COUNT(*) = 2 INTO _cols_exist
  FROM information_schema.columns
  WHERE table_name = 'chatbot_config'
    AND column_name IN ('button_text_mappings', 'button_id_mappings');

  IF NOT _cols_exist THEN
    RAISE NOTICE 'Step 5 skipped: button_text_mappings / button_id_mappings columns do not exist in chatbot_config. No legacy data to migrate.';
  ELSE
    FOR _tenant IN
      SELECT tenant_id, button_text_mappings, button_id_mappings
      FROM chatbot_config
      WHERE button_text_mappings IS NOT NULL
         OR button_id_mappings IS NOT NULL
    LOOP
      -- Migrate button_text_mappings
      IF _tenant.button_text_mappings IS NOT NULL THEN
        FOR _key, _val IN
          SELECT key, value::text FROM jsonb_each_text(_tenant.button_text_mappings)
        LOOP
          INSERT INTO chatbot_button_mappings (tenant_id, button_text, template_name, is_active)
          VALUES (_tenant.tenant_id, _key, _val, TRUE)
          ON CONFLICT DO NOTHING;
        END LOOP;
      END IF;

      -- Migrate button_id_mappings
      IF _tenant.button_id_mappings IS NOT NULL THEN
        FOR _key, _val IN
          SELECT key, value::text FROM jsonb_each_text(_tenant.button_id_mappings)
        LOOP
          UPDATE chatbot_button_mappings
            SET button_id = _key
            WHERE tenant_id = _tenant.tenant_id
              AND template_name = _val
              AND button_id = '';

          IF NOT FOUND THEN
            INSERT INTO chatbot_button_mappings (tenant_id, button_id, template_name, is_active)
            VALUES (_tenant.tenant_id, _key, _val, TRUE)
            ON CONFLICT DO NOTHING;
          END IF;
        END LOOP;
      END IF;
    END LOOP;
  END IF;
END$$;


-- ── 6. Migrate fallback template for tenants that had chatbot enabled ─
UPDATE chatbot_config
SET fallback_template_name = 'first_trigger'
WHERE is_enabled = TRUE
  AND (fallback_template_name = '' OR fallback_template_name IS NULL);
