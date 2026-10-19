-- Compact projection only. Existing projects/snapshots and their RLS stay authoritative.
-- Reapplying this migration is safe; no user data is changed.
create or replace function public.pf_project_preview(p_sequence text, p_workspace jsonb, p_prefix_length integer)
returns jsonb language sql immutable security invoker set search_path = '' as $$
  with snapshot as (
    select p_workspace->'descriptors' as d, p_workspace->'alignment' as a,
      p_workspace->'source' as s, greatest(0, least(p_prefix_length, 40)) as cap
  ), evidence as (
    select *, a->'result' as r,
      length(p_sequence) - length(translate(p_sequence, 'UOBZX', '')) as excluded
    from snapshot
  ), verified as (
    select *,
      d->>'sequence' = p_sequence
        and d->>'methodVersion' = 'proteinforge-descriptors-v1'
        and d->>'excluded' = excluded::text
        and d->'result'->>'length' = (length(p_sequence) - excluded)::text as descriptors_match,
      a->>'first' <> '' and a->>'second' <> ''
        and r->'parameters'->>'matrix' = a->>'matrix'
        and r->'parameters'->>'mode' = a->>'mode'
        and r->'parameters'->>'gapOpen' = '10'
        and r->'parameters'->>'gapExtend' = '0.5'
        and r->'parameters'->>'terminalGaps' = case when a->>'mode' = 'global' then 'free' else 'not-applicable' end
        and length(r->>'alignedFirst') > 0
        and length(r->>'alignedFirst')::text = r->>'columns'
        and length(r->>'alignedSecond')::text = r->>'columns'
        and length(r->>'marks')::text = r->>'columns'
        and r->>'marks' !~ '[^|: ]'
        and (length(r->>'marks') - length(replace(r->>'marks', '|', '')))::text = r->>'identities'
        and (length(r->>'marks') - length(translate(r->>'marks', '|:', '')))::text = r->>'similarities'
        and case when a->>'mode' = 'global' then
          replace(r->>'alignedFirst', '-', '') = translate(a->>'first', 'UO', 'CK')
          and replace(r->>'alignedSecond', '-', '') = translate(a->>'second', 'UO', 'CK')
        else
          position(replace(r->>'alignedFirst', '-', '') in translate(a->>'first', 'UO', 'CK')) > 0
          and position(replace(r->>'alignedSecond', '-', '') in translate(a->>'second', 'UO', 'CK')) > 0
        end as alignment_matches
    from evidence
  )
  select jsonb_build_object(
    'sequenceLength', length(p_sequence), 'sequencePrefix', left(p_sequence, cap),
    'excluded', excluded,
    'descriptors', case when descriptors_match then d->'result' else null end,
    'source', case when s->>'sequence' = p_sequence then jsonb_build_object('origin', s->>'origin', 'accession', s->>'accession') else null end,
    'alignment', case when coalesce(a->>'first', '') <> '' or coalesce(a->>'second', '') <> '' then jsonb_build_object(
      'firstLength', length(coalesce(a->>'first', '')), 'secondLength', length(coalesce(a->>'second', '')),
      'firstPrefix', left(coalesce(a->>'first', ''), cap), 'secondPrefix', left(coalesce(a->>'second', ''), cap),
      'matrix', a->>'matrix', 'mode', a->>'mode',
      'result', case when alignment_matches then jsonb_build_object('identities', r->'identities', 'similarities', r->'similarities', 'columns', r->'columns') else null end
    ) else null end
  ) from verified;
$$;

create or replace function public.pf_list_projects(
  p_limit integer, p_offset integer, p_query text, p_order text, p_prefix_length integer
) returns table (id uuid, name text, status text, created_at timestamptz, updated_at timestamptz, preview jsonb)
language plpgsql stable security invoker set search_path = '' as $$
begin
  if auth.uid() is null then raise sqlstate 'PT401' using message = 'Authentication required'; end if;
  if p_limit is null or p_limit not between 1 and 101
     or p_offset is null or p_offset not between 0 and 100000
     or p_query is null or length(p_query) > 120
     or p_order is null or p_order not in ('recent', 'name')
     or p_prefix_length is null or p_prefix_length not between 1 and 40 then
    raise sqlstate 'PT422' using message = 'Invalid project query';
  end if;
  return query
    select p.id, p.name, p.status, p.created_at, p.updated_at,
      public.pf_project_preview(p.active_sequence, a.results, p_prefix_length)
    from public.projects p
    left join public.analyses a on a.project_id = p.id and a.type = 'workspace-v1'
    where p.owner_id = (select auth.uid()) and position(lower(btrim(p_query)) in lower(p.name)) > 0
    order by
      case when p_order = 'name' then lower(p.name) end asc,
      case when p_order = 'name' then p.id end asc,
      case when p_order = 'recent' then p.updated_at end desc,
      p.id desc
    limit p_limit offset p_offset;
end;
$$;

revoke all on function public.pf_project_preview(text,jsonb,integer) from public, anon;
revoke all on function public.pf_list_projects(integer,integer,text,text,integer) from public, anon;
grant execute on function public.pf_project_preview(text,jsonb,integer) to authenticated;
grant execute on function public.pf_list_projects(integer,integer,text,text,integer) to authenticated;
notify pgrst, 'reload schema';
