# supabase

- `migrations/` — versioned SQL. `20261125000000_initial_schema.sql` is a **draft for review**; it has not been applied to any Supabase project.
- `tests/auth_stub.sql` — test-only stand-in for Supabase's `auth` schema so the migration can be loaded into plain Postgres + pgvector.
- `tests/rls_isolation_test.sql` — checks sign-up bootstrap, tenant isolation (read and direct cross-tenant write), face-identity constraint, HNSW index use.

Run locally (any Postgres 17 + pgvector 0.8):
```
createdb iv && psql -d iv -c "alter database iv set search_path = public, extensions"
psql -d iv -f tests/auth_stub.sql -f migrations/20261125000000_initial_schema.sql -f tests/rls_isolation_test.sql
```
With the Supabase CLI, use `supabase db reset` instead and skip the auth stub.
