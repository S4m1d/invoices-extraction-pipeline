-- Read-only access for the tester (integrator/specs/data-model.md, Access).
-- The role is created by infra/; without it the grant is skipped (the service logs a warning).

-- +goose Up
-- +goose StatementBegin
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'tester') THEN
        GRANT SELECT ON invoices, invoice_lines TO tester;
    END IF;
END
$$;
-- +goose StatementEnd
