-- Mounted at /docker-entrypoint-initdb.d/init-scripts/98-realtime.sql and run
-- once, when Postgres initializes an empty data directory (as Supabase's own
-- self-hosting setup does). Supabase Realtime connects as supabase_admin with
-- search_path _realtime (DB_AFTER_CONNECT_QUERY in docker-compose.gpu.yml) and
-- creates its migrations table there, so the schema must exist first.
create schema if not exists _realtime;
alter schema _realtime owner to supabase_admin;
