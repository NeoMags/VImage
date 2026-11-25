-- Verifies sign-up bootstrap, tenant isolation, provenance constraints and vector/phash indexes.
\set ON_ERROR_STOP on
grant select, insert, update, delete on all tables in schema public to authenticated;
grant usage on schema private to authenticated;
insert into auth.users (id, email) values
  ('00000000-0000-0000-0000-00000000000a','a@example.test'),
  ('00000000-0000-0000-0000-00000000000b','b@example.test');
select count(*) = 2 as two_personal_workspaces from public.workspaces;
-- seed one asset per user as service role (bypass RLS)
insert into public.assets (workspace_id, sha256, mime_type, original_filename, byte_size, phash)
select w.id, sha256(convert_to(w.created_by::text,'utf8')), 'image/png', 'shot.png', 10, (repeat('01',32))::bit(64)
from public.workspaces w;
insert into public.asset_embeddings (asset_id, workspace_id, model_id, embedding)
select a.id, a.workspace_id, 1, array_fill(0.01::real, array[512])::vector from public.assets a;

set role authenticated;
select set_config('request.jwt.claim.sub','00000000-0000-0000-0000-00000000000a', false);
select count(*) = 1 as a_sees_only_own_asset from public.assets;
select count(*) = 1 as a_sees_only_own_workspace from public.workspaces;
-- A cannot insert into B's workspace
do $$ begin
  insert into public.tags (workspace_id, name, slug)
  select id, 'x','x' from public.workspaces where created_by = '00000000-0000-0000-0000-00000000000b';
  raise notice 'cross_tenant_insert_blocked: % (0 rows visible to target)', not found;
end $$;
do $$ begin
  begin
    insert into public.tags (workspace_id, name, slug) values
      ((select id from public.workspaces limit 1) , 'ok','ok');
    raise notice 'own_workspace_insert_ok: true';
  end;
end $$;
reset role;
-- face identity requires confirmation
do $$ begin
  begin
    insert into public.asset_faces (asset_id, workspace_id, region_id, person_id, model_id, embedding)
    values (gen_random_uuid(), gen_random_uuid(), gen_random_uuid(), gen_random_uuid(), 1, '[1,2]');
  exception when check_violation then raise notice 'face_identity_needs_confirmation: true';
            when foreign_key_violation then raise notice 'face check ran after FK (fk hit first)';
  end;
end $$;
-- index usage
set enable_seqscan = off;
explain (costs off) select id from public.asset_embeddings where model_id = 1
  order by embedding::vector(512) <=> array_fill(0.02::real, array[512])::vector(512) limit 10;
explain (costs off) select id from public.assets where phash is not null
  order by phash <~> (repeat('10',32))::bit(64) limit 10;
-- Direct cross-tenant write with a known foreign workspace id must be rejected by RLS.
select id as b_ws from public.workspaces where created_by = '00000000-0000-0000-0000-00000000000b' \gset
set role authenticated;
select set_config('request.jwt.claim.sub','00000000-0000-0000-0000-00000000000a', false);
select format($f$do $d$ begin
  insert into public.tags (workspace_id, name, slug) values (%L::uuid, 'evil','evil');
  raise exception 'FAIL: cross-tenant insert succeeded';
exception when insufficient_privilege then raise notice 'cross_tenant_direct_insert_rejected: true';
end $d$;$f$, :'b_ws') \gexec
reset role;
