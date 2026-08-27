#!/bin/sh
set -eu

temporal_password="$(cat /run/secrets/temporal_db_password)"
run_schema() {
  database="$1"
  schema_directory="$2"
  temporal-sql-tool \
    --plugin postgres12 \
    --ep temporal-postgres \
    --port 5432 \
    -u temporal \
    --pw "$temporal_password" \
    --db "$database" \
    setup-schema -v 0.0
  temporal-sql-tool \
    --plugin postgres12 \
    --ep temporal-postgres \
    --port 5432 \
    -u temporal \
    --pw "$temporal_password" \
    --db "$database" \
    update-schema -d "$schema_directory"
}

run_schema temporal /etc/temporal/schema/postgresql/v12/temporal/versioned
run_schema temporal_visibility /etc/temporal/schema/postgresql/v12/visibility/versioned
