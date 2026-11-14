-- How long a read-only link lasts, and which projects have one, without a request per project.
--
-- pf_share_project gains a three-argument form that takes the link's duration in days and accepts only
-- 1, 7 or 30 (PROJECT_SHARE_DURATIONS_DAYS in app/project_config.py). The two-argument form stays for
-- clients that send no duration and keeps giving 7 days (PROJECT_SHARE_LIFETIME_DAYS). Both keep the
-- MFA check of 20261113000002, so this migration applies after it.
--
-- pf_list_projects_with_links returns what pf_list_projects returns plus the expiry of the project's
-- unexpired link. It is a new function rather than a new column because the published API refuses
-- unknown fields in a listing row: applying this before the new API must not break the current one.
-- Idempotent: create or replace, and the grants are repeated.

create or replace function public.pf_share_project(p_id uuid, p_token_sha256 text, p_days integer)
returns jsonb language plpgsql security definer set search_path = '' as $$
declare
  shared public.project_shares%rowtype;
begin
  if auth.uid() is null then raise sqlstate 'PT401' using message = 'Authentication required'; end if;
  if not public.pf_session_meets_mfa() then raise sqlstate 'PT401' using message = 'MFA required'; end if;
  if p_token_sha256 is null or p_token_sha256 !~ '^[a-f0-9]{64}$' then
    raise sqlstate 'PT422' using message = 'Invalid token';
  end if;
  if p_days is null or p_days not in (1, 7, 30) then -- PROJECT_SHARE_DURATIONS_DAYS
    raise sqlstate 'PT422' using message = 'Invalid duration';
  end if;
  perform 1 from public.projects where id = p_id and owner_id = auth.uid();
  if not found then raise sqlstate 'PT404' using message = 'Project not found'; end if;
  insert into public.project_shares(project_id, owner_id, token_sha256, created_at, expires_at)
  values (p_id, auth.uid(), p_token_sha256, now(), now() + make_interval(days => p_days))
  on conflict (project_id) do update set token_sha256 = excluded.token_sha256, created_at = excluded.created_at, expires_at = excluded.expires_at
  returning * into shared;
  return jsonb_build_object('createdAt', shared.created_at, 'expiresAt', shared.expires_at);
end;
$$;

-- Clients that send no duration keep the 7 days they always had.
create or replace function public.pf_share_project(p_id uuid, p_token_sha256 text)
returns jsonb language sql security definer set search_path = '' as $$
  select public.pf_share_project(p_id, p_token_sha256, 7); -- PROJECT_SHARE_LIFETIME_DAYS
$$;

create or replace function public.pf_list_projects_with_links(
  p_limit integer, p_offset integer, p_query text, p_order text, p_prefix_length integer
) returns table (id uuid, name text, status text, created_at timestamptz, updated_at timestamptz, preview jsonb, share_expires_at timestamptz)
language sql stable security invoker set search_path = '' as $$
  -- Same validation, owner filter and order as pf_list_projects; the link is read under the owner's RLS.
  select l.id, l.name, l.status, l.created_at, l.updated_at, l.preview, s.expires_at
    from public.pf_list_projects(p_limit, p_offset, p_query, p_order, p_prefix_length) with ordinality as l(id, name, status, created_at, updated_at, preview, position)
    left join public.project_shares s on s.project_id = l.id and s.expires_at > now()
    order by l.position;
$$;

revoke all on function public.pf_share_project(uuid,text,integer) from public, anon;
revoke all on function public.pf_list_projects_with_links(integer,integer,text,text,integer) from public, anon;
grant execute on function public.pf_share_project(uuid,text,integer) to authenticated;
grant execute on function public.pf_share_project(uuid,text) to authenticated;
grant execute on function public.pf_list_projects_with_links(integer,integer,text,text,integer) to authenticated;
notify pgrst, 'reload schema';
