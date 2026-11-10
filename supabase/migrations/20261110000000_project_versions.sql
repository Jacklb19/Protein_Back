-- Earlier versions of a project. Every save that changes the sequence or the snapshot keeps the
-- content it replaces, up to five versions per project; restoring one keeps the current content too,
-- so a restore can be undone. A rename alone is not a version.
--
-- Only the server writes versions: the table grants owners nothing but reading their own rows, and
-- the one writer, pf_keep_project_version, copies what is stored now and takes nothing from the caller.
-- Owner RLS, invoker privileges, validation and optimistic locking of pf_save_project stay as in
-- 20261102000000; this migration repeats that function with the version step added.

create table if not exists public.project_versions (
  id uuid primary key default gen_random_uuid(),
  project_id uuid not null references public.projects(id) on delete cascade,
  owner_id uuid not null references auth.users(id) on delete cascade,
  active_sequence text not null,
  workspace jsonb not null,
  -- When this content was saved: the project's update time while it was current.
  saved_at timestamptz not null,
  kept_at timestamptz not null default now()
);
create index if not exists idx_project_versions_project_saved on public.project_versions(project_id, saved_at desc, id desc);

alter table public.project_versions enable row level security;
revoke all on public.project_versions from anon, authenticated;
grant select on public.project_versions to authenticated;
drop policy if exists "Users can view versions of own projects" on public.project_versions;
create policy "Users can view versions of own projects"
  on public.project_versions for select to authenticated
  using (owner_id = (select auth.uid()));

-- Copies the caller's project as it is stored now and drops the oldest copies beyond the limit.
-- `p_replacing` is the version a restore puts back: it becomes the current content, so it is dropped.
-- Definer rights write the table owners cannot; the copy is of the caller's own row, so calling it
-- directly only keeps a version of what that person already has.
drop function if exists public.pf_keep_project_version(uuid);
create or replace function public.pf_keep_project_version(p_id uuid, p_replacing uuid default null)
returns void language plpgsql security definer set search_path = '' as $$
begin
  if auth.uid() is null then raise sqlstate 'PT401' using message = 'Authentication required'; end if;
  delete from public.project_versions
    where id = p_replacing and project_id = p_id and owner_id = auth.uid();
  insert into public.project_versions(project_id, owner_id, active_sequence, workspace, saved_at)
  select p.id, p.owner_id, p.active_sequence, a.results, p.updated_at
    from public.projects p
    join public.analyses a on a.project_id = p.id and a.type = 'workspace-v1'
    where p.id = p_id and p.owner_id = auth.uid();
  delete from public.project_versions v
    where v.project_id = p_id and v.id not in (
      select kept.id from public.project_versions kept
        where kept.project_id = p_id order by kept.saved_at desc, kept.id desc limit 5 -- PROJECT_VERSION_LIMIT
    );
end;
$$;

create or replace function public.pf_save_project(
  p_id uuid, p_name text, p_sequence text, p_workspace jsonb, p_expected_updated_at timestamptz,
  p_create_request_id uuid default null
) returns jsonb language plpgsql security invoker set search_path = '' as $$
declare
  saved_id uuid;
  previous public.projects%rowtype;
  stored_version text;
  stored_results jsonb;
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
    select results, results->>'schemaVersion' into stored_results, stored_version from public.analyses
      where project_id = p_id and type = 'workspace-v1';
    if stored_version ~ '^[0-9]{1,3}$' and stored_version::int > (p_workspace->>'schemaVersion')::int then
      raise sqlstate 'PT409' using message = 'Project requires current client';
    end if;
    -- The content about to be replaced becomes a version; a rename alone keeps none.
    if previous.active_sequence is distinct from p_sequence or stored_results is distinct from p_workspace then
      perform public.pf_keep_project_version(p_id);
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

-- The caller's versions of one project, newest first, as compact previews: never the full snapshot.
create or replace function public.pf_list_project_versions(p_id uuid, p_prefix_length integer)
returns jsonb language sql stable security invoker set search_path = '' as $$
  select coalesce(jsonb_agg(jsonb_build_object(
      'id', v.id, 'savedAt', v.saved_at,
      'preview', public.pf_project_preview(v.active_sequence, v.workspace, p_prefix_length)
    ) order by v.saved_at desc, v.id desc), '[]'::jsonb)
    from public.project_versions v
    where v.project_id = p_id and v.owner_id = (select auth.uid());
$$;

-- Puts a version back as the project's content, keeping the name. What was current becomes a
-- version first, so the restore can be undone. The version was a valid snapshot when it was saved,
-- so it may carry an earlier schema version than the one stored now: a deliberate restore from the
-- current client is the one write allowed to lower it.
create or replace function public.pf_restore_project_version(p_project_id uuid, p_version_id uuid, p_expected_updated_at timestamptz)
returns jsonb language plpgsql security invoker set search_path = '' as $$
declare
  kept public.project_versions%rowtype;
  previous public.projects%rowtype;
begin
  if auth.uid() is null then raise sqlstate 'PT401' using message = 'Authentication required'; end if;
  select * into kept from public.project_versions
    where id = p_version_id and project_id = p_project_id and owner_id = auth.uid();
  if not found then raise sqlstate 'PT404' using message = 'Version not found'; end if;
  select * into previous from public.projects
    where id = kept.project_id and owner_id = auth.uid() for update;
  if not found then raise sqlstate 'PT404' using message = 'Version not found'; end if;
  if p_expected_updated_at is null or previous.updated_at <> p_expected_updated_at then
    raise sqlstate 'PT409' using message = 'Project changed';
  end if;
  -- The version restored becomes the current content, so it does not stay listed twice.
  perform public.pf_keep_project_version(previous.id, p_version_id);
  update public.projects set active_sequence = kept.active_sequence where id = previous.id;
  insert into public.analyses(project_id, type, parameters, results)
  values (previous.id, 'workspace-v1', jsonb_build_object('schemaVersion', kept.workspace->'schemaVersion'), kept.workspace)
  on conflict (project_id) where type = 'workspace-v1'
  do update set results = excluded.results, parameters = excluded.parameters;
  return public.pf_project_document(previous.id);
end;
$$;

revoke all on function public.pf_keep_project_version(uuid,uuid) from public, anon;
revoke all on function public.pf_save_project(uuid,text,text,jsonb,timestamptz,uuid) from public, anon;
revoke all on function public.pf_list_project_versions(uuid,integer) from public, anon;
revoke all on function public.pf_restore_project_version(uuid,uuid,timestamptz) from public, anon;
grant execute on function public.pf_keep_project_version(uuid,uuid) to authenticated;
grant execute on function public.pf_save_project(uuid,text,text,jsonb,timestamptz,uuid) to authenticated;
grant execute on function public.pf_list_project_versions(uuid,integer) to authenticated;
grant execute on function public.pf_restore_project_version(uuid,uuid,timestamptz) to authenticated;
notify pgrst, 'reload schema';
