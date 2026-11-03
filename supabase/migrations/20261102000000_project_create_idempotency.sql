-- Idempotent CreateProject: an optional client request id per owner. A retry after a lost
-- response returns the project the first call created instead of inserting a duplicate.
-- Owner RLS, invoker privileges, validation and optimistic locking stay as in 20261030000000.

alter table public.projects add column if not exists create_request_id uuid;
create unique index if not exists idx_projects_owner_create_request
  on public.projects(owner_id, create_request_id) where create_request_id is not null;

-- The new parameter has a default, so callers that send the previous five arguments keep
-- working; dropping the old signature avoids an ambiguous overload in PostgREST.
drop function if exists public.pf_save_project(uuid,text,text,jsonb,timestamptz);

create or replace function public.pf_save_project(
  p_id uuid, p_name text, p_sequence text, p_workspace jsonb, p_expected_updated_at timestamptz,
  p_create_request_id uuid default null
) returns jsonb language plpgsql security invoker set search_path = '' as $$
declare
  saved_id uuid;
  previous public.projects%rowtype;
  stored_version text;
begin
  if auth.uid() is null then raise sqlstate 'PT401' using message = 'Authentication required'; end if;
  if p_name is null or length(btrim(p_name)) not between 1 and 120
     or p_sequence is null or length(p_sequence) > 100000
     or p_sequence !~ '^[ACDEFGHIKLMNPQRSTVWYUOBZX]*$'
     or jsonb_typeof(p_workspace) is distinct from 'object'
     or p_workspace->>'schemaVersion' not in ('1', '2', '3', '4')
     or p_workspace->>'schemaVersion' is null
     or jsonb_typeof(p_workspace->'schemaVersion') is distinct from 'number'
     or octet_length(p_workspace::text) > 1900000
     or (p_id is not null and p_create_request_id is not null) then
    raise sqlstate 'PT422' using message = 'Invalid workspace';
  end if;
  perform public.pf_validate_laboratory(p_workspace);
  perform public.pf_validate_generator_history(p_workspace);
  if p_id is null then
    -- A concurrent or repeated create with the same pair waits for the first one and then
    -- inserts nothing; the stored project is returned as it is now, snapshot untouched.
    insert into public.projects(owner_id, name, active_sequence, create_request_id)
    values (auth.uid(), btrim(p_name), p_sequence, p_create_request_id)
    on conflict (owner_id, create_request_id) where create_request_id is not null do nothing
    returning id into saved_id;
    if saved_id is null then
      select id into saved_id from public.projects
        where owner_id = auth.uid() and create_request_id = p_create_request_id;
      return public.pf_project_document(saved_id);
    end if;
  else
    -- Lock and compare in the same transaction; concurrent writers cannot both succeed.
    select * into previous from public.projects
      where id = p_id and owner_id = auth.uid() for update;
    if not found then raise sqlstate 'PT404' using message = 'Project not found'; end if;
    if p_expected_updated_at is null or previous.updated_at <> p_expected_updated_at then
      raise sqlstate 'PT409' using message = 'Project changed';
    end if;
    -- Monotonic guard: a client never replaces a snapshot version it does not understand.
    select results->>'schemaVersion' into stored_version from public.analyses
      where project_id = p_id and type = 'workspace-v1';
    if stored_version ~ '^[0-9]{1,3}$' and stored_version::int > (p_workspace->>'schemaVersion')::int then
      raise sqlstate 'PT409' using message = 'Project requires current client';
    end if;
    update public.projects set name = btrim(p_name), active_sequence = p_sequence where id = p_id;
    saved_id := p_id;
  end if;
  insert into public.analyses(project_id, type, parameters, results)
  values (saved_id, 'workspace-v1', jsonb_build_object('schemaVersion', p_workspace->'schemaVersion'), p_workspace)
  on conflict (project_id) where type = 'workspace-v1'
  do update set results = excluded.results, parameters = excluded.parameters;
  return public.pf_project_document(saved_id);
end;
$$;

revoke all on function public.pf_save_project(uuid,text,text,jsonb,timestamptz,uuid) from public, anon;
grant execute on function public.pf_save_project(uuid,text,text,jsonb,timestamptz,uuid) to authenticated;
notify pgrst, 'reload schema';
