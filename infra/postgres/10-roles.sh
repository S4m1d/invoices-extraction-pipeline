#!/bin/bash
# Roles and database (infra/README.md, Initialisation). Runs only on an empty data directory.
set -euo pipefail
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" \
  -v integrator_password="$INTEGRATOR_DB_PASSWORD" -v tester_password="$TESTER_DB_PASSWORD" <<'SQL'
CREATE ROLE integrator LOGIN PASSWORD :'integrator_password';
CREATE ROLE tester LOGIN PASSWORD :'tester_password';
CREATE DATABASE invoices OWNER integrator;
SQL
