#!/usr/bin/env node
// Prints the public schema of a migrated database as JSON, for
// apps/api/tests/integration/test_schema_matches_code.py.
//
// Usage: DATABASE_URL=postgresql://... node scripts/verify/db-schema.mjs
// Reports relations and columns, the privileges the API roles hold on them,
// foreign-key relationships (for PostgREST embedding), and every overload of
// each public function with its argument names, defaults and return relation.
import pg from "pg";

const ROLES = ["service_role", "authenticated"];
const connectionString = process.env.DATABASE_URL;
if (!connectionString) {
  console.error("DATABASE_URL is required");
  process.exit(2);
}

const client = new pg.Client({ connectionString });
await client.connect();
try {
  const relations = await client.query(`
    select c.relname as name,
           case c.relkind when 'r' then 'table' when 'p' then 'table' when 'v' then 'view'
                          when 'm' then 'materialized_view' else c.relkind::text end as kind
      from pg_class c
      join pg_namespace n on n.oid = c.relnamespace
     where n.nspname = 'public' and c.relkind in ('r', 'p', 'v', 'm')
     order by 1`);
  const columns = await client.query(
    `
    select c.relname as relation, a.attname as column_name, r.role,
           has_column_privilege(r.role, c.oid, a.attname, 'SELECT') as can_select,
           has_column_privilege(r.role, c.oid, a.attname, 'INSERT') as can_insert,
           has_column_privilege(r.role, c.oid, a.attname, 'UPDATE') as can_update
      from pg_class c
      join pg_namespace n on n.oid = c.relnamespace
      join pg_attribute a on a.attrelid = c.oid and a.attnum > 0 and not a.attisdropped
     cross join unnest($1::text[]) as r(role)
     where n.nspname = 'public' and c.relkind in ('r', 'p', 'v', 'm')
     order by 1, a.attnum`,
    [ROLES],
  );
  const tablePrivileges = await client.query(
    `
    select c.relname as relation, r.role,
           has_table_privilege(r.role, c.oid, 'DELETE') as can_delete
      from pg_class c
      join pg_namespace n on n.oid = c.relnamespace
     cross join unnest($1::text[]) as r(role)
     where n.nspname = 'public' and c.relkind in ('r', 'p', 'v', 'm')`,
    [ROLES],
  );
  const foreignKeys = await client.query(`
    select src.relname as source, dst.relname as target
      from pg_constraint k
      join pg_class src on src.oid = k.conrelid
      join pg_class dst on dst.oid = k.confrelid
      join pg_namespace sn on sn.oid = src.relnamespace
      join pg_namespace dn on dn.oid = dst.relnamespace
     where k.contype = 'f' and sn.nspname = 'public' and dn.nspname = 'public'`);
  const functions = await client.query(
    `
    select p.oid::regprocedure::text as signature,
           p.proname as name,
           coalesce(p.proargnames, array[]::text[]) as arg_names,
           coalesce(p.proargmodes::text[], array[]::text[]) as arg_modes,
           p.pronargs as input_count,
           p.pronargdefaults as default_count,
           p.proretset as returns_set,
           rt.typname as return_type,
           rel.relname as return_relation,
           array(
             select r.role from unnest($1::text[]) as r(role)
              where has_function_privilege(r.role, p.oid, 'EXECUTE')
           ) as executable_by
      from pg_proc p
      join pg_namespace n on n.oid = p.pronamespace
      join pg_type rt on rt.oid = p.prorettype
      left join pg_class rel on rel.oid = rt.typrelid and rt.typrelid <> 0
     where n.nspname = 'public' and p.prokind = 'f'
     order by p.proname, signature`,
    [ROLES],
  );

  const schema = { relations: {}, foreign_keys: [], functions: {} };
  for (const row of relations.rows) {
    schema.relations[row.name] = { kind: row.kind, columns: {}, delete: {} };
  }
  for (const row of columns.rows) {
    const relation = schema.relations[row.relation];
    relation.columns[row.column_name] ??= {};
    relation.columns[row.column_name][row.role] = {
      select: row.can_select,
      insert: row.can_insert,
      update: row.can_update,
    };
  }
  for (const row of tablePrivileges.rows) {
    schema.relations[row.relation].delete[row.role] = row.can_delete;
  }
  schema.foreign_keys = foreignKeys.rows.map((row) => [row.source, row.target]);
  for (const row of functions.rows) {
    // Only IN/INOUT/VARIADIC arguments are passed by PostgREST callers.
    const inputs = [];
    row.arg_names.forEach((argName, index) => {
      const mode = row.arg_modes[index] || "i";
      if (mode === "i" || mode === "b" || mode === "v") inputs.push(argName);
    });
    schema.functions[row.name] ??= [];
    schema.functions[row.name].push({
      signature: row.signature,
      args: inputs,
      required: inputs.slice(0, row.input_count - row.default_count),
      returns_set: row.returns_set,
      return_type: row.return_type,
      return_relation: row.return_relation,
      executable_by: row.executable_by,
    });
  }
  process.stdout.write(`${JSON.stringify(schema)}\n`);
} finally {
  await client.end();
}
