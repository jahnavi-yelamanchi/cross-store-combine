-- Append-only enforcement for ledger_events on Postgres.
CREATE OR REPLACE FUNCTION ledger_events_append_only() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'append-only: % on ledger_events is not allowed', TG_OP;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS ledger_events_no_update_delete ON ledger_events;
CREATE TRIGGER ledger_events_no_update_delete
    BEFORE UPDATE OR DELETE ON ledger_events
    FOR EACH ROW EXECUTE FUNCTION ledger_events_append_only();

DROP TRIGGER IF EXISTS ledger_events_no_truncate ON ledger_events;
CREATE TRIGGER ledger_events_no_truncate
    BEFORE TRUNCATE ON ledger_events
    FOR EACH STATEMENT EXECUTE FUNCTION ledger_events_append_only();
