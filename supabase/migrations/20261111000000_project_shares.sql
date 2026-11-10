-- Read-only links to a project. One link per project: creating another replaces it, and deleting
-- the project or the link ends it. A link lasts seven days.
--
-- The database never holds a link: the API generates a random token, gives it to the owner once and
-- stores only its SHA-256 here. Anyone who presents a token whose hash matches an unexpired row reads
-- the project's analysis and comparison through pf_shared_project; the laboratory, saved searches and
-- anything about the owner are not part of it. Owners read their own rows; only definer functions write.

create table if not exists public.project_shares (
  project_id uuid primary key references public.projects(id) on delete cascade,
  owner_id uuid not null references auth.users(id) on delete cascade,
  token_sha256 text not null unique check (token_sha256 ~ '^[a-f0-9]{64}$'),
  created_at timestamptz not null default now(),
  expires_at timestamptz not null
);

alter table public.project_shares enable row level security;
revoke all on public.project_shares from anon, authenticated;
grant select on public.project_shares to authenticated;
drop policy if exists "Users can view links of own projects" on public.project_shares;
create policy "Users can view links of own projects"
  on public.project_shares for select to authenticated
  using (owner_id = (select auth.uid()));

-- Starts, or replaces, the link of the caller's project. The previous token stops working at once.
create or replace function public.pf_share_project(p_id uuid, p_token_sha256 text)
returns jsonb language plpgsql security definer set search_path = '' as $$
declare
  shared public.project_shares%rowtype;
begin
  if auth.uid() is null then raise sqlstate 'PT401' using message = 'Authentication required'; end if;
  if p_token_sha256 is null or p_token_sha256 !~ '^[a-f0-9]{64}$' then
    raise sqlstate 'PT422' using message = 'Invalid token';
  end if;
  perform 1 from public.projects where id = p_id and owner_id = auth.uid();
  if not found then raise sqlstate 'PT404' using message = 'Project not found'; end if;
  insert into public.project_shares(project_id, owner_id, token_sha256, created_at, expires_at)
  values (p_id, auth.uid(), p_token_sha256, now(), now() + interval '7 days') -- PROJECT_SHARE_LIFETIME_DAYS
  on conflict (project_id) do update set token_sha256 = excluded.token_sha256, created_at = excluded.created_at, expires_at = excluded.expires_at
  returning * into shared;
  return jsonb_build_object('createdAt', shared.created_at, 'expiresAt', shared.expires_at);
end;
$$;

-- The caller's current link of a project, without its hash; null when there is none or it expired.
create or replace function public.pf_project_share(p_id uuid)
returns jsonb language sql stable security invoker set search_path = '' as $$
  select jsonb_build_object('createdAt', s.created_at, 'expiresAt', s.expires_at)
    from public.project_shares s
    where s.project_id = p_id and s.owner_id = (select auth.uid()) and s.expires_at > now();
$$;

create or replace function public.pf_unshare_project(p_id uuid)
returns void language plpgsql security definer set search_path = '' as $$
begin
  if auth.uid() is null then raise sqlstate 'PT401' using message = 'Authentication required'; end if;
  delete from public.project_shares where project_id = p_id and owner_id = auth.uid();
end;
$$;

-- What a link shows: name, sequence, analysis and comparison as a schema-1 snapshot. Null when no
-- unexpired link has that hash, so a wrong, revoked and expired token look the same.
create or replace function public.pf_shared_project(p_token_sha256 text)
returns jsonb language sql stable security definer set search_path = '' as $$
  select jsonb_build_object(
      'name', p.name, 'activeSequence', p.active_sequence, 'updatedAt', p.updated_at, 'expiresAt', s.expires_at,
      'workspace', jsonb_build_object(
        'schemaVersion', 1,
        'profileWindow', coalesce(a.results->'profileWindow', '9'::jsonb),
        'source', coalesce(a.results->'source', 'null'::jsonb),
        'descriptors', coalesce(a.results->'descriptors', 'null'::jsonb),
        'alignment', coalesce(a.results->'alignment', '{"first":"","second":"","matrix":"BLOSUM62","mode":"global","result":null}'::jsonb)
      )
    )
    from public.project_shares s
    join public.projects p on p.id = s.project_id
    left join public.analyses a on a.project_id = p.id and a.type = 'workspace-v1'
    where p_token_sha256 ~ '^[a-f0-9]{64}$' and s.token_sha256 = p_token_sha256 and s.expires_at > now();
$$;

revoke all on function public.pf_share_project(uuid,text) from public, anon;
revoke all on function public.pf_project_share(uuid) from public, anon;
revoke all on function public.pf_unshare_project(uuid) from public, anon;
revoke all on function public.pf_shared_project(text) from public;
grant execute on function public.pf_share_project(uuid,text) to authenticated;
grant execute on function public.pf_project_share(uuid) to authenticated;
grant execute on function public.pf_unshare_project(uuid) to authenticated;
-- The one function a visitor without an account may call, and only with a link's hash.
grant execute on function public.pf_shared_project(text) to anon, authenticated;
notify pgrst, 'reload schema';
