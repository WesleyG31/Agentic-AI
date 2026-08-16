-- Local Kompass runtime schema. The postgres superuser is migration/admin only;
-- application traffic uses kompass_app and is subject to FORCE RLS.

DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'kompass_app') THEN
    CREATE ROLE kompass_app LOGIN PASSWORD 'kompass-local-app-only'
      NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOBYPASSRLS;
  END IF;
END
$$;

CREATE OR REPLACE FUNCTION kompass_current_tenant() RETURNS text
LANGUAGE plpgsql STABLE
AS $$
DECLARE
  tenant text;
BEGIN
  tenant := current_setting('app.current_tenant', true);
  IF tenant IS NULL OR btrim(tenant) = '' THEN
    RAISE EXCEPTION 'tenant context is required' USING ERRCODE = '42501';
  END IF;
  RETURN tenant;
END
$$;

CREATE TABLE IF NOT EXISTS action_receipts (
  tenant_id text NOT NULL CHECK (length(tenant_id) BETWEEN 1 AND 128),
  idempotency_key text NOT NULL CHECK (length(idempotency_key) BETWEEN 1 AND 256),
  action text NOT NULL CHECK (length(action) BETWEEN 1 AND 128),
  request_hash char(64) NOT NULL,
  request_json jsonb,
  status text NOT NULL CHECK (status IN (
    'processing', 'denied', 'approval_required', 'rejected', 'succeeded',
    'verification_failed', 'compensated', 'failed'
  )),
  attempts integer NOT NULL DEFAULT 0 CHECK (attempts BETWEEN 0 AND 3),
  result_json jsonb,
  verified boolean NOT NULL DEFAULT false,
  authorization_reason text NOT NULL DEFAULT '',
  error text,
  compensation_reference text,
  created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  PRIMARY KEY (tenant_id, idempotency_key)
);

CREATE INDEX IF NOT EXISTS idx_action_receipts_reconcile
  ON action_receipts (tenant_id, status, updated_at);

CREATE TABLE IF NOT EXISTS workflow_threads (
  tenant_id text NOT NULL CHECK (length(tenant_id) BETWEEN 1 AND 128),
  user_id text NOT NULL CHECK (length(user_id) BETWEEN 1 AND 256),
  storage_thread_id char(64) NOT NULL,
  created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  PRIMARY KEY (tenant_id, storage_thread_id)
);

ALTER TABLE action_receipts ENABLE ROW LEVEL SECURITY;
ALTER TABLE action_receipts FORCE ROW LEVEL SECURITY;
ALTER TABLE workflow_threads ENABLE ROW LEVEL SECURITY;
ALTER TABLE workflow_threads FORCE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS action_receipts_tenant ON action_receipts;
CREATE POLICY action_receipts_tenant ON action_receipts
  FOR ALL TO kompass_app
  USING (tenant_id = kompass_current_tenant())
  WITH CHECK (tenant_id = kompass_current_tenant());

DROP POLICY IF EXISTS workflow_threads_tenant ON workflow_threads;
CREATE POLICY workflow_threads_tenant ON workflow_threads
  FOR ALL TO kompass_app
  USING (tenant_id = kompass_current_tenant())
  WITH CHECK (tenant_id = kompass_current_tenant());

REVOKE ALL ON action_receipts, workflow_threads FROM PUBLIC;
GRANT SELECT, INSERT, UPDATE, DELETE ON action_receipts, workflow_threads TO kompass_app;
REVOKE ALL ON FUNCTION kompass_current_tenant() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION kompass_current_tenant() TO kompass_app;
