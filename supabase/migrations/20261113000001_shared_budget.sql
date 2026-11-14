-- Shared spending budgets. Until now each serverless instance counted its own window, so N instances
-- allowed N times the budget. pf_spend counts one window per account and scope in PostgreSQL, atomically,
-- plus an optional window shared by everyone (the assistant's daily cap, prediction's overall rate).
--
-- The API calls these functions with the caller's JWT and the public key, never a service role, so the
-- database charges auth.uid() and nobody else. Whoever calls pf_spend directly only spends their own
-- budget; the per-scope ceilings below bound what that can add to a global window. The API turns it on
-- with SHARED_BUDGET_ENABLED=enabled once this migration is applied, and falls back to its in-process
-- counters whenever these functions cannot answer (app/spend.py).
--
-- Values repeated from app/spend_config.py: scopes, ceilings (assistant 60, uniprot 120, folding 3 per
-- window), the global ceiling 100000 and window lengths of 1 s to 86400 s.
-- Idempotent: create if not exists and create or replace.

create table if not exists public.spend_windows (
  scope text not null check (scope in ('assistant', 'uniprot', 'folding')),
  -- The account, or the all-zero uuid for the window everyone shares.
  subject uuid not null,
  started_at timestamptz not null,
  used integer not null check (used >= 0),
  primary key (scope, subject)
);

-- Private: no grants and no policies. Only the definer functions below read or write it.
alter table public.spend_windows enable row level security;
revoke all on public.spend_windows from public, anon, authenticated;

-- Charges one request to the caller and, when p_global_limit > 0, to the shared window too. A window
-- starts with the first request and lasts p_window_seconds, like the in-process counter it replaces.
-- p_limit = 0 means no per-account limit of its own; the scope ceiling still applies.
-- Returns {"allowed": boolean, "remaining": integer} where remaining is the caller's window.
create or replace function public.pf_spend(
  p_scope text, p_limit integer, p_window_seconds integer,
  p_global_limit integer default 0, p_global_window_seconds integer default 86400
) returns jsonb language plpgsql volatile security definer set search_path = '' as $$
declare
  caller uuid := auth.uid();
  everyone constant uuid := '00000000-0000-0000-0000-000000000000';
  scope_ceiling integer;
  caller_limit integer;
  caller_used integer;
  global_used integer;
begin
  if caller is null then raise sqlstate 'PT401' using message = 'Authentication required'; end if;
  scope_ceiling := case p_scope when 'assistant' then 60 when 'uniprot' then 120 when 'folding' then 3 end;
  if scope_ceiling is null or p_limit is null or p_limit < 0
     or p_window_seconds is null or p_window_seconds not between 1 and 86400
     or p_global_limit is null or p_global_limit not between 0 and 100000
     or p_global_window_seconds is null or p_global_window_seconds not between 1 and 86400 then
    raise sqlstate 'PT422' using message = 'Invalid spend';
  end if;
  caller_limit := case when p_limit = 0 then scope_ceiling else least(p_limit, scope_ceiling) end;

  -- One statement: concurrent requests of the same account queue on its row, and the condition is
  -- checked again on the latest version, so at most caller_limit of them pass in a window.
  insert into public.spend_windows as w (scope, subject, started_at, used)
  values (p_scope, caller, now(), 1)
  on conflict (scope, subject) do update
    set started_at = case when w.started_at <= now() - make_interval(secs => p_window_seconds) then now() else w.started_at end,
        used = case when w.started_at <= now() - make_interval(secs => p_window_seconds) then 1 else w.used + 1 end
    where w.started_at <= now() - make_interval(secs => p_window_seconds) or w.used < caller_limit
  returning w.used into caller_used;
  if caller_used is null then
    return jsonb_build_object('allowed', false, 'remaining', 0);
  end if;

  if p_global_limit > 0 then
    insert into public.spend_windows as w (scope, subject, started_at, used)
    values (p_scope, everyone, now(), 1)
    on conflict (scope, subject) do update
      set started_at = case when w.started_at <= now() - make_interval(secs => p_global_window_seconds) then now() else w.started_at end,
          used = case when w.started_at <= now() - make_interval(secs => p_global_window_seconds) then 1 else w.used + 1 end
      where w.started_at <= now() - make_interval(secs => p_global_window_seconds) or w.used < p_global_limit
    returning w.used into global_used;
    if global_used is null then
      -- Refused by the shared window: give the caller's request back. The row is still locked by this
      -- transaction, so nobody saw the charge.
      update public.spend_windows set used = used - 1 where scope = p_scope and subject = caller;
      return jsonb_build_object('allowed', false, 'remaining', caller_limit - caller_used + 1);
    end if;
  end if;
  return jsonb_build_object('allowed', true, 'remaining', caller_limit - caller_used);
end;
$$;

-- What the caller may still spend in its current window, without charging anything.
create or replace function public.pf_spend_remaining(p_scope text, p_limit integer, p_window_seconds integer)
returns integer language plpgsql stable security definer set search_path = '' as $$
declare
  scope_ceiling integer;
  caller_limit integer;
  stored public.spend_windows%rowtype;
begin
  if auth.uid() is null then raise sqlstate 'PT401' using message = 'Authentication required'; end if;
  scope_ceiling := case p_scope when 'assistant' then 60 when 'uniprot' then 120 when 'folding' then 3 end;
  if scope_ceiling is null or p_limit is null or p_limit < 0 or p_window_seconds is null or p_window_seconds not between 1 and 86400 then
    raise sqlstate 'PT422' using message = 'Invalid spend';
  end if;
  caller_limit := case when p_limit = 0 then scope_ceiling else least(p_limit, scope_ceiling) end;
  select * into stored from public.spend_windows where scope = p_scope and subject = auth.uid();
  if not found or stored.started_at <= now() - make_interval(secs => p_window_seconds) then
    return caller_limit;
  end if;
  return greatest(0, caller_limit - stored.used);
end;
$$;

revoke all on function public.pf_spend(text, integer, integer, integer, integer) from public, anon;
revoke all on function public.pf_spend_remaining(text, integer, integer) from public, anon;
grant execute on function public.pf_spend(text, integer, integer, integer, integer) to authenticated;
grant execute on function public.pf_spend_remaining(text, integer, integer) to authenticated;
