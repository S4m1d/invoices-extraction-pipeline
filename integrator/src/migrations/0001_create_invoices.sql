-- Procurement tables (integrator/specs/data-model.md).

-- +goose Up
CREATE TABLE invoices (
    id               uuid PRIMARY KEY,
    document_id      uuid NOT NULL,
    correlation_id   uuid NOT NULL,
    supplier_name    text NOT NULL,
    recipient_name   text,
    invoice_number   text NOT NULL,
    issue_date       date NOT NULL,
    due_date         date,
    currency         char(3) NOT NULL,
    subtotal         numeric,
    tax_total        numeric,
    grand_total      numeric NOT NULL,
    tax_rate         numeric,
    extraction_model text,
    prompt_version   text,
    created_at       timestamptz NOT NULL,
    updated_at       timestamptz NOT NULL,
    CONSTRAINT invoices_document_id_key UNIQUE (document_id),
    CONSTRAINT invoices_currency_check CHECK (currency ~ '^[A-Z]{3}$')
);

CREATE TABLE invoice_lines (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    invoice_id  uuid NOT NULL REFERENCES invoices (id) ON DELETE CASCADE,
    line_no     int NOT NULL,
    description text NOT NULL,
    quantity    numeric,
    unit_price  numeric,
    amount      numeric NOT NULL,
    CONSTRAINT invoice_lines_invoice_id_line_no_key UNIQUE (invoice_id, line_no),
    CONSTRAINT invoice_lines_line_no_check CHECK (line_no >= 1)
);
