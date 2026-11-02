-- Reports generated in the browser: a private Storage bucket per owner folder and a registry table.
-- Limits repeat app/report_config.py and are enforced here for direct PostgREST/Storage calls too.
-- Storage rows stay read-only for SQL: objects are uploaded and deleted only through the Storage API.

insert into storage.buckets (id, name, public, file_size_limit, allowed_mime_types)
values ('reports', 'reports', false, 5000000, array['application/pdf', 'application/json', 'text/csv'])
on conflict (id) do update set public = excluded.public, file_size_limit = excluded.file_size_limit,
  allowed_mime_types = excluded.allowed_mime_types;

create table if not exists public.reports (
  id uuid primary key default public.uuid_generate_v7(),
  owner_id uuid not null references public.profiles(id) on delete cascade,
  -- A deleted project leaves its reports unlinked: SQL cannot remove their Storage objects.
  project_id uuid references public.projects(id) on delete set null,
  kind text not null,
  size_bytes integer not null,
  sha256 text not null,
  object_path text not null,
  status text not null default 'pending',
  created_at timestamptz not null default now(),
  completed_at timestamptz,
  constraint reports_kind check (kind in ('pdf', 'json', 'profile-csv', 'alignment-csv')),
  constraint reports_size check (size_bytes between 1 and 5000000),
  constraint reports_sha256 check (sha256 ~ '^[a-f0-9]{64}$'),
  constraint reports_status check (status in ('pending', 'ready')),
  constraint reports_completion check ((status = 'ready') = (completed_at is not null)),
  constraint reports_object_path unique (object_path)
);
create index if not exists idx_reports_owner_created on public.reports(owner_id, created_at desc, id desc);
create index if not exists idx_reports_project_id on public.reports(project_id);
alter table public.reports enable row level security;

revoke all on public.reports from anon;
grant select, insert, update, delete on public.reports to authenticated;

drop policy if exists "Users can view own reports" on public.reports;
create policy "Users can view own reports"
  on public.reports for select to authenticated
  using ((select auth.uid()) = owner_id);

drop policy if exists "Users can insert own reports" on public.reports;
create policy "Users can insert own reports"
  on public.reports for insert to authenticated
  with check (
    (select auth.uid()) = owner_id
    and (project_id is null or exists (
      select 1 from public.projects where projects.id = reports.project_id and projects.owner_id = (select auth.uid())
    ))
  );

drop policy if exists "Users can update own reports" on public.reports;
create policy "Users can update own reports"
  on public.reports for update to authenticated
  using ((select auth.uid()) = owner_id)
  with check ((select auth.uid()) = owner_id);

drop policy if exists "Users can delete own reports" on public.reports;
create policy "Users can delete own reports"
  on public.reports for delete to authenticated
  using ((select auth.uid()) = owner_id);

-- Server-owned columns, input bounds and the per-owner quota hold for any insert path.
create or replace function public.pf_report_before_insert()
returns trigger language plpgsql security invoker set search_path = '' as $$
declare
  caller uuid := auth.uid();
  stored_count integer;
  stored_bytes bigint;
begin
  if caller is null then raise sqlstate 'PT401' using message = 'Authentication required'; end if;
  if new.kind is null or new.kind not in ('pdf', 'json', 'profile-csv', 'alignment-csv')
     or new.size_bytes is null or new.size_bytes not between 1 and 5000000
     or new.sha256 is null or new.sha256 !~ '^[a-f0-9]{64}$' then
    raise sqlstate 'PT422' using message = 'Invalid report';
  end if;
  if new.project_id is not null and not exists (
    select 1 from public.projects p where p.id = new.project_id and p.owner_id = caller
  ) then
    raise sqlstate 'PT404' using message = 'Project not found';
  end if;
  -- One owner's reservations are serialized, so concurrent requests cannot both pass the quota.
  perform pg_advisory_xact_lock(hashtextextended('pf_reports:' || caller::text, 0));
  select count(*), coalesce(sum(r.size_bytes), 0) into stored_count, stored_bytes
    from public.reports r where r.owner_id = caller;
  if stored_count >= 20 or stored_bytes + new.size_bytes > 25000000 then
    raise sqlstate 'PT413' using message = 'Report quota exceeded';
  end if;
  new.id := public.uuid_generate_v7();
  new.owner_id := caller;
  new.status := 'pending';
  new.created_at := now();
  new.completed_at := null;
  new.object_path := caller::text || '/' || new.id::text
    || case new.kind when 'pdf' then '.pdf' when 'json' then '.json' else '.csv' end;
  return new;
end;
$$;

-- Records are immutable except the single pending -> ready step, which needs the uploaded object,
-- and the unlink performed by deleting a project.
create or replace function public.pf_report_before_update()
returns trigger language plpgsql security invoker set search_path = '' as $$
declare stored jsonb;
begin
  if (new.id, new.owner_id, new.kind, new.size_bytes, new.sha256, new.object_path, new.created_at)
       is distinct from (old.id, old.owner_id, old.kind, old.size_bytes, old.sha256, old.object_path, old.created_at)
     or (new.project_id is distinct from old.project_id and new.project_id is not null) then
    raise sqlstate 'PT422' using message = 'Invalid report';
  end if;
  if new.status is not distinct from old.status then
    new.completed_at := old.completed_at;
    return new;
  end if;
  if old.status <> 'pending' or new.status is distinct from 'ready' then
    raise sqlstate 'PT422' using message = 'Invalid report';
  end if;
  -- Storage records the bytes it received; size and media type must match the reservation.
  select o.metadata into stored from storage.objects o
    where o.bucket_id = 'reports' and o.name = old.object_path;
  if stored is null
     or stored->>'size' is distinct from old.size_bytes::text
     or lower(btrim(split_part(stored->>'mimetype', ';', 1))) is distinct from
       (case old.kind when 'pdf' then 'application/pdf' when 'json' then 'application/json' else 'text/csv' end) then
    raise sqlstate 'PT409' using message = 'Report upload missing';
  end if;
  new.completed_at := now();
  return new;
end;
$$;

create or replace trigger pf_report_before_insert before insert on public.reports
  for each row execute function public.pf_report_before_insert();
create or replace trigger pf_report_before_update before update on public.reports
  for each row execute function public.pf_report_before_update();

-- API shape of a record; the object path stays internal.
create or replace function public.pf_report_json(r public.reports)
returns jsonb language sql stable security invoker set search_path = '' as $$
  select jsonb_build_object(
    'id', r.id, 'projectId', r.project_id, 'kind', r.kind, 'sizeBytes', r.size_bytes, 'sha256', r.sha256,
    'status', r.status, 'createdAt', r.created_at, 'completedAt', r.completed_at
  );
$$;

create or replace function public.pf_reserve_report(p_project_id uuid, p_kind text, p_size_bytes integer, p_sha256 text)
returns jsonb language plpgsql security invoker set search_path = '' as $$
declare saved public.reports%rowtype;
begin
  if auth.uid() is null then raise sqlstate 'PT401' using message = 'Authentication required'; end if;
  -- Unused reservations end with their two-hour upload URL. An uploaded object keeps its
  -- reservation until it is completed or deleted, because only the Storage API can remove it.
  delete from public.reports r
    where r.owner_id = auth.uid() and r.status = 'pending' and r.created_at < now() - interval '7200 seconds'
      and not exists (select 1 from storage.objects o where o.bucket_id = 'reports' and o.name = r.object_path);
  insert into public.reports(project_id, kind, size_bytes, sha256)
    values (p_project_id, p_kind, p_size_bytes, p_sha256) returning * into saved;
  return jsonb_build_object('report', public.pf_report_json(saved), 'objectPath', saved.object_path);
end;
$$;

-- Idempotent: completing a ready report returns it unchanged.
create or replace function public.pf_complete_report(p_id uuid)
returns jsonb language plpgsql security invoker set search_path = '' as $$
declare saved public.reports%rowtype;
begin
  if auth.uid() is null then raise sqlstate 'PT401' using message = 'Authentication required'; end if;
  select * into saved from public.reports where id = p_id and owner_id = auth.uid() for update;
  if not found then raise sqlstate 'PT404' using message = 'Report not found'; end if;
  if saved.status = 'pending' then
    update public.reports set status = 'ready' where id = p_id returning * into saved;
  end if;
  return public.pf_report_json(saved);
end;
$$;

create or replace function public.pf_list_reports(p_limit integer, p_offset integer, p_project_id uuid)
returns table (id uuid, project_id uuid, kind text, size_bytes integer, sha256 text, status text, created_at timestamptz, completed_at timestamptz)
language plpgsql stable security invoker set search_path = '' as $$
begin
  if auth.uid() is null then raise sqlstate 'PT401' using message = 'Authentication required'; end if;
  if p_limit is null or p_limit not between 1 and 51 or p_offset is null or p_offset not between 0 and 1000 then
    raise sqlstate 'PT422' using message = 'Invalid report query';
  end if;
  return query
    select r.id, r.project_id, r.kind, r.size_bytes, r.sha256, r.status, r.created_at, r.completed_at
    from public.reports r
    where r.owner_id = (select auth.uid()) and (p_project_id is null or r.project_id = p_project_id)
    order by r.created_at desc, r.id desc
    limit p_limit offset p_offset;
end;
$$;

-- Record plus object path, for signing a download or deleting the object; null when not owned.
create or replace function public.pf_report_object(p_id uuid)
returns jsonb language sql stable security invoker set search_path = '' as $$
  select jsonb_build_object('report', public.pf_report_json(r), 'objectPath', r.object_path)
  from public.reports r where r.id = p_id and r.owner_id = (select auth.uid());
$$;

create or replace function public.pf_delete_report(p_id uuid)
returns jsonb language plpgsql security invoker set search_path = '' as $$
begin
  if auth.uid() is null then raise sqlstate 'PT401' using message = 'Authentication required'; end if;
  delete from public.reports where id = p_id and owner_id = auth.uid();
  if not found then raise sqlstate 'PT404' using message = 'Report not found'; end if;
  return '{}'::jsonb;
end;
$$;

-- Storage objects: each owner reads and deletes only its own folder, and may create an object
-- only at the path of one of its own pending, unexpired reservations. No update policy: no overwrite.
drop policy if exists "Owners can read own report objects" on storage.objects;
create policy "Owners can read own report objects"
  on storage.objects for select to authenticated
  using (bucket_id = 'reports' and (storage.foldername(name))[1] = (select auth.uid())::text);

drop policy if exists "Owners can upload reserved report objects" on storage.objects;
create policy "Owners can upload reserved report objects"
  on storage.objects for insert to authenticated
  with check (
    bucket_id = 'reports' and (storage.foldername(name))[1] = (select auth.uid())::text
    and exists (
      select 1 from public.reports r
      where r.object_path = objects.name and r.owner_id = (select auth.uid())
        and r.status = 'pending' and r.created_at > now() - interval '7200 seconds'
    )
  );

drop policy if exists "Owners can delete own report objects" on storage.objects;
create policy "Owners can delete own report objects"
  on storage.objects for delete to authenticated
  using (bucket_id = 'reports' and (storage.foldername(name))[1] = (select auth.uid())::text);

revoke all on function public.pf_report_before_insert(), public.pf_report_before_update() from public, anon;
revoke all on function public.pf_report_json(public.reports), public.pf_reserve_report(uuid,text,integer,text),
  public.pf_complete_report(uuid), public.pf_list_reports(integer,integer,uuid), public.pf_report_object(uuid),
  public.pf_delete_report(uuid) from public, anon;
grant execute on function public.pf_report_json(public.reports), public.pf_reserve_report(uuid,text,integer,text),
  public.pf_complete_report(uuid), public.pf_list_reports(integer,integer,uuid), public.pf_report_object(uuid),
  public.pf_delete_report(uuid) to authenticated;
notify pgrst, 'reload schema';
