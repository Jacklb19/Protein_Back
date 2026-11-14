-- Per-account project quota: a new project is refused once its owner already has 50. Each project keeps
-- up to 1.9 MB of snapshot plus five earlier versions, so an unbounded count is the storage risk.
--
-- The check is a BEFORE INSERT trigger rather than a fourth copy of pf_save_project: every create path
-- (pf_save_project and a direct insert under RLS) passes through it, and updates never do, so accounts
-- already above the limit keep every project and can still edit, rename, restore or delete them.
-- Counting happens under a per-owner advisory lock held until commit, as the report quota does in
-- 20261101000000, so two concurrent creates cannot both take the last place.
--
-- A repeated create with a request id already stored (20261102000000) is not a new project: the insert
-- in pf_save_project does nothing on conflict and returns the stored one, so the quota lets it through.
-- Idempotent: create or replace and drop trigger if exists.

create or replace function public.pf_project_before_insert_quota()
returns trigger language plpgsql security invoker set search_path = '' as $$
declare
  stored_count integer;
begin
  perform pg_advisory_xact_lock(hashtextextended('pf_projects:' || new.owner_id::text, 0));
  if new.create_request_id is not null and exists (
    select 1 from public.projects p where p.owner_id = new.owner_id and p.create_request_id = new.create_request_id
  ) then
    return new;
  end if;
  select count(*) into stored_count from public.projects p where p.owner_id = new.owner_id;
  -- 50 = PROJECT_LIMIT in app/project_config.py; the message is matched there and never carries data.
  if stored_count >= 50 then
    raise sqlstate 'PT413' using message = 'Project quota exceeded';
  end if;
  return new;
end;
$$;

revoke all on function public.pf_project_before_insert_quota() from public, anon, authenticated;

drop trigger if exists pf_project_quota on public.projects;
create trigger pf_project_quota
  before insert on public.projects
  for each row execute function public.pf_project_before_insert_quota();
