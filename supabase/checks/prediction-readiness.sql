-- Read-only installation checks. No project data, identities or secrets are returned.
-- These markers do not prove Auth/PostgREST wiring, ownership isolation or provider availability.
with functions as (
  select p.proname, p.prosecdef, pg_get_functiondef(p.oid) as definition
  from pg_catalog.pg_proc p
  join pg_catalog.pg_namespace n on n.oid = p.pronamespace
  where n.nspname = 'public'
    and p.proname in ('pf_save_project','pf_validate_laboratory','pf_validate_laboratory_v1','pf_validate_prediction')
), protected_tables as (
  select c.relname, c.relrowsecurity
  from pg_catalog.pg_class c
  join pg_catalog.pg_namespace n on n.oid = c.relnamespace
  where n.nspname = 'public' and c.relname in ('projects','analyses') and c.relkind = 'r'
)
select
  exists(select 1 from functions where proname='pf_validate_prediction') as prediction_validator_installed,
  exists(select 1 from functions where proname='pf_validate_laboratory'
         and strpos(definition,'pf_validate_prediction') > 0) as laboratory_v2_marker,
  exists(select 1 from functions where proname='pf_save_project'
         and strpos(definition,'''4''') > 0
         and strpos(definition,'Project requires current client') > 0) as workspace_v4_guard_marker,
  (select count(*)=4 and coalesce(bool_and(not prosecdef),false) from functions) as functions_are_invoker,
  (select count(*)=2 and coalesce(bool_and(relrowsecurity),false) from protected_tables) as project_tables_have_rls;
