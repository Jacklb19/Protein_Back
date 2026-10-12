-- Workspace snapshots use the existing analyses table; one current snapshot per project.
-- Keep UUIDv7 compatible with PostgreSQL 15+ and an empty RPC search_path, without pgcrypto.
create or replace function public.uuid_generate_v7()
returns uuid language plpgsql volatile security invoker set search_path = '' as $$
declare uuid_bytes bytea;
begin
  uuid_bytes := substring(int8send(floor(extract(epoch from clock_timestamp()) * 1000)::bigint) from 3 for 6)
    || substring(uuid_send(gen_random_uuid()) from 1 for 10);
  uuid_bytes := set_byte(uuid_bytes, 6, (get_byte(uuid_bytes, 6) & 15) | 112);
  uuid_bytes := set_byte(uuid_bytes, 8, (get_byte(uuid_bytes, 8) & 63) | 128);
  return encode(uuid_bytes, 'hex')::uuid;
end;
$$;
create unique index idx_analyses_workspace on public.analyses(project_id) where type = 'workspace-v1';
create index idx_projects_owner_updated on public.projects(owner_id, updated_at desc, id desc);

-- Explicit grants also work on Supabase installations without default table privileges.
revoke all on public.projects, public.analyses, public.alignments from anon;
grant select, insert, update, delete on public.projects, public.analyses, public.alignments to authenticated;
grant select, insert, update on public.profiles to authenticated;

create or replace function public.pf_project_updated_at()
returns trigger language plpgsql security invoker set search_path = '' as $$
begin
  new.updated_at := greatest(clock_timestamp(), old.updated_at + interval '1 microsecond');
  return new;
end;
$$;
create trigger pf_project_updated_at before update on public.projects
  for each row execute function public.pf_project_updated_at();

create or replace function public.pf_project_document(p_id uuid)
returns jsonb language sql stable security invoker set search_path = '' as $$
  select jsonb_build_object(
    'id', p.id, 'name', p.name, 'status', p.status,
    'activeSequence', p.active_sequence, 'createdAt', p.created_at, 'updatedAt', p.updated_at,
    'workspace', coalesce(a.results, '{"schemaVersion":1,"profileWindow":9,"descriptors":null,"alignment":{"first":"","second":"","matrix":"BLOSUM62","mode":"global","result":null}}'::jsonb)
  ) from public.projects p
  left join public.analyses a on a.project_id = p.id and a.type = 'workspace-v1'
  where p.id = p_id and p.owner_id = (select auth.uid());
$$;

create or replace function public.pf_save_project(
  p_id uuid, p_name text, p_sequence text, p_workspace jsonb, p_expected_updated_at timestamptz
) returns jsonb language plpgsql security invoker set search_path = '' as $$
declare
  saved_id uuid;
  previous public.projects%rowtype;
begin
  if auth.uid() is null then raise sqlstate 'PT401' using message = 'Authentication required'; end if;
  if p_name is null or length(btrim(p_name)) not between 1 and 120
     or p_sequence is null or length(p_sequence) > 100000
     or p_sequence !~ '^[ACDEFGHIKLMNPQRSTVWYUOBZX]*$'
     or jsonb_typeof(p_workspace) is distinct from 'object'
     or p_workspace->>'schemaVersion' is distinct from '1'
     or octet_length(p_workspace::text) > 1000000 then
    raise sqlstate 'PT422' using message = 'Invalid workspace';
  end if;
  if p_id is null then
    insert into public.projects(owner_id, name, active_sequence)
    values (auth.uid(), btrim(p_name), p_sequence) returning id into saved_id;
  else
    -- Lock and compare in the same transaction; concurrent writers cannot both succeed.
    select * into previous from public.projects
      where id = p_id and owner_id = auth.uid() for update;
    if not found then raise sqlstate 'PT404' using message = 'Project not found'; end if;
    if p_expected_updated_at is null or previous.updated_at <> p_expected_updated_at then
      raise sqlstate 'PT409' using message = 'Project changed';
    end if;
    update public.projects set name = btrim(p_name), active_sequence = p_sequence where id = p_id;
    saved_id := p_id;
  end if;
  insert into public.analyses(project_id, type, parameters, results)
  values (saved_id, 'workspace-v1', '{"schemaVersion":1}'::jsonb, p_workspace)
  on conflict (project_id) where type = 'workspace-v1'
  do update set results = excluded.results, parameters = excluded.parameters;
  return public.pf_project_document(saved_id);
end;
$$;

create or replace function public.pf_delete_project(p_id uuid, p_expected_updated_at timestamptz)
returns jsonb language plpgsql security invoker set search_path = '' as $$
declare previous public.projects%rowtype;
begin
  select * into previous from public.projects where id = p_id and owner_id = auth.uid() for update;
  if not found then raise sqlstate 'PT404' using message = 'Project not found'; end if;
  if p_expected_updated_at is null or previous.updated_at <> p_expected_updated_at then
    raise sqlstate 'PT409' using message = 'Project changed';
  end if;
  delete from public.projects where id = p_id;
  return '{}'::jsonb;
end;
$$;

revoke all on function public.pf_project_document(uuid) from public, anon;
revoke all on function public.pf_save_project(uuid,text,text,jsonb,timestamptz) from public, anon;
revoke all on function public.pf_delete_project(uuid,timestamptz) from public, anon;
revoke all on function public.pf_project_updated_at() from public, anon;
grant execute on function public.pf_project_document(uuid) to authenticated;
grant execute on function public.pf_save_project(uuid,text,text,jsonb,timestamptz) to authenticated;
grant execute on function public.pf_delete_project(uuid,timestamptz) to authenticated;
notify pgrst, 'reload schema';
