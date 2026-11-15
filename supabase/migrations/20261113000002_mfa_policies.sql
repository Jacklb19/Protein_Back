-- Row-level MFA for accounts that enrolled a factor, the opt-in pattern from Supabase's MFA guide
-- ("Enforce for users who have opted in"): a session must be aal2 only when its account has a verified
-- factor; accounts without one keep working at aal1. The API already refuses those sessions
-- (app/identity.py, mfa-required); these policies hold the same rule for direct PostgREST and Storage
-- calls made with the public key and a user's JWT.
--
-- The policies are AS RESTRICTIVE, so they narrow the existing permissive owner policies and grant
-- nothing. They apply to `authenticated` only: anonymous link reads (pf_shared_project) are untouched,
-- and SECURITY DEFINER functions run as the table owner, which RLS does not restrict.
--
-- Unlike the guide's inline subquery, the check lives in one SECURITY DEFINER function so it does not
-- depend on `authenticated` being able to read auth.mfa_factors. A JWT without an aal claim counts as
-- aal1. Idempotent: create or replace and drop policy if exists.

create or replace function public.pf_session_meets_mfa()
returns boolean language sql stable security definer set search_path = '' as $$
  select coalesce(auth.jwt()->>'aal', 'aal1') = 'aal2'
    or not exists (
      select 1 from auth.mfa_factors f where f.user_id = auth.uid() and f.status = 'verified'
    );
$$;
revoke all on function public.pf_session_meets_mfa() from public, anon;
-- Policies run with the caller's privileges, so the caller must be able to execute the check.
grant execute on function public.pf_session_meets_mfa() to authenticated;

do $$
declare
  protected text;
begin
  foreach protected in array array['projects', 'analyses', 'reports', 'project_versions', 'project_shares'] loop
    execute format('drop policy if exists "Require MFA once enrolled" on public.%I', protected);
    execute format(
      'create policy "Require MFA once enrolled" on public.%I as restrictive for all to authenticated '
      'using ((select public.pf_session_meets_mfa())) with check ((select public.pf_session_meets_mfa()))',
      protected
    );
  end loop;
end;
$$;

-- Only the reports bucket; other buckets keep their own rules.
drop policy if exists "Require MFA once enrolled for reports" on storage.objects;
create policy "Require MFA once enrolled for reports"
  on storage.objects as restrictive for all to authenticated
  using (bucket_id <> 'reports' or (select public.pf_session_meets_mfa()))
  with check (bucket_id <> 'reports' or (select public.pf_session_meets_mfa()));

-- SECURITY DEFINER functions that write for the caller bypass RLS, so the restrictive policies cannot
-- reach them. The three that authenticated callers may run directly get the same check; otherwise an
-- aal1 session of an enrolled account could start a link and read its own project anonymously, or drop
-- versions. Bodies as in 20261110000000 and 20261111000000 with that line added; grants are kept.
-- pf_shared_project (anonymous) and pf_spend (own budget only) need no session check.
create or replace function public.pf_keep_project_version(p_id uuid, p_replacing uuid default null)
returns void language plpgsql security definer set search_path = '' as $$
begin
  if auth.uid() is null then raise sqlstate 'PT401' using message = 'Authentication required'; end if;
  if not public.pf_session_meets_mfa() then raise sqlstate 'PT401' using message = 'MFA required'; end if;
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

create or replace function public.pf_share_project(p_id uuid, p_token_sha256 text)
returns jsonb language plpgsql security definer set search_path = '' as $$
declare
  shared public.project_shares%rowtype;
begin
  if auth.uid() is null then raise sqlstate 'PT401' using message = 'Authentication required'; end if;
  if not public.pf_session_meets_mfa() then raise sqlstate 'PT401' using message = 'MFA required'; end if;
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

create or replace function public.pf_unshare_project(p_id uuid)
returns void language plpgsql security definer set search_path = '' as $$
begin
  if auth.uid() is null then raise sqlstate 'PT401' using message = 'Authentication required'; end if;
  if not public.pf_session_meets_mfa() then raise sqlstate 'PT401' using message = 'MFA required'; end if;
  delete from public.project_shares where project_id = p_id and owner_id = auth.uid();
end;
$$;
