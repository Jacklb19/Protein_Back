-- Table constraints behind the grants of 20261012000000. `authenticated` keeps its table privileges,
-- because the pf_* functions run as SECURITY INVOKER and write with them; a direct PostgREST write
-- now meets the same bounds as the API and pf_save_project instead of none.
-- Values mirror app/project_config.py (name), app/projects.py (sequence, statuses, workspace versions)
-- and pf_save_project in 20261102000000 (snapshot bytes); tests/test_migrations.py keeps them in step.
-- NOT VALID: rows already stored are not rechecked, so applying this cannot fail on remote data;
-- every new insert or update is checked. `alter table ... validate constraint` can follow later.
-- Idempotent: a constraint is added only when the table does not have one with that name yet.

do $$
declare
  item record;
begin
  for item in select * from (values
    ('public.projects'::regclass, 'projects_name',
      $c$check (length(name) between 1 and 120 and length(btrim(name)) >= 1 and name !~ '[\x01-\x1f]')$c$),
    ('public.projects'::regclass, 'projects_active_sequence',
      $c$check (length(active_sequence) <= 100000 and active_sequence ~ '^[ACDEFGHIKLMNPQRSTVWYUOBZX]*$')$c$),
    ('public.projects'::regclass, 'projects_status',
      $c$check (status in ('draft'))$c$),
    -- The workspace snapshot is the only analysis the application writes.
    ('public.analyses'::regclass, 'analyses_type',
      $c$check (type in ('workspace-v1'))$c$),
    -- coalesce: a missing schemaVersion makes the expression NULL, which a CHECK would accept.
    ('public.analyses'::regclass, 'analyses_workspace',
      $c$check (coalesce(jsonb_typeof(results) = 'object'
        and jsonb_typeof(results->'schemaVersion') = 'number'
        and results->>'schemaVersion' in ('1', '2', '3', '4')
        and octet_length(results::text) <= 1900000, false))$c$),
    ('public.analyses'::regclass, 'analyses_parameters',
      $c$check (parameters = jsonb_build_object('schemaVersion', results->'schemaVersion'))$c$)
  ) as constraints(table_name, constraint_name, definition)
  loop
    if not exists (select 1 from pg_constraint where conrelid = item.table_name and conname = item.constraint_name) then
      execute format('alter table %s add constraint %I %s not valid', item.table_name, item.constraint_name, item.definition);
    end if;
  end loop;
end;
$$;

-- No code path reads or writes alignments: neither the API, the pf_* functions nor the frontend.
-- Owners keep select and delete on their rows; new rows can no longer be written directly.
revoke insert, update on public.alignments from authenticated;
