-- Mounted at /docker-entrypoint-initdb.d/init-scripts/99-roles.sql and run once,
-- when Postgres initializes an empty data directory. The image's own init
-- scripts create these login roles without passwords; Auth (GoTrue), REST
-- (PostgREST) and Storage log in as them over the network with the Postgres
-- password (see GOTRUE_DB_DATABASE_URL, PGRST_DB_URI and the Storage
-- DATABASE_URL in docker-compose.gpu.yml). Init scripts run as the postgres OS
-- user; the image entrypoint exports POSTGRES_PASSWORD from the
-- postgres_password secret (POSTGRES_PASSWORD_FILE) before dropping root.
\set pgpass `echo "$POSTGRES_PASSWORD"`

alter user authenticator with password :'pgpass';
alter user supabase_auth_admin with password :'pgpass';
alter user supabase_storage_admin with password :'pgpass';
