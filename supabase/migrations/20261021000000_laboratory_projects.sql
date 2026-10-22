-- Approved portable laboratory persistence; no new tables or storage buckets.
create or replace function public.pf_laboratory_variant_count(w jsonb)
returns integer language plpgsql immutable security invoker set search_path='' as $$
begin
  return least(3, (select count(*)::integer from jsonb_object_keys((w->'laboratory'->>'documentJson')::jsonb->'state'->'drafts')));
exception when others then return 0;
end;
$$;

create or replace function public.pf_validate_laboratory(w jsonb)
returns void language plpgsql immutable security invoker set search_path='' as $$
declare l jsonb; d jsonb; s jsonb; entry record; pos jsonb; n integer;
begin
  l := w->'laboratory';
  if w->>'schemaVersion'='1' then
    if l is not null and l <> 'null'::jsonb then raise exception 'Invalid laboratory'; end if;
    return;
  end if;
  if jsonb_typeof(l) is distinct from 'object' or l->>'schemaVersion' is distinct from '1'
     or jsonb_typeof(l->'documentJson') is distinct from 'string'
     or octet_length(l->>'documentJson') > 1000000
     or l->>'documentSha256' is distinct from encode(sha256(convert_to(l->>'documentJson','UTF8')),'hex') then
    raise exception 'Invalid laboratory';
  end if;
  d := (l->>'documentJson')::jsonb;
  s := d->'state';
  if d->>'kind' is distinct from 'proteinforge-laboratory' or d->>'schemaVersion' is distinct from '1'
     or jsonb_typeof(d->'source') is distinct from 'object'
     or jsonb_typeof(s->'drafts') is distinct from 'object'
     or (select count(*) from jsonb_object_keys(s->'drafts')) not between 1 and 3
     or not (s->'drafts' ? (s->>'activeId'))
     or s->>'selected' !~ '^(0|[1-9][0-9]{0,2})$' or s->>'selected' is null then
    raise exception 'Invalid laboratory';
  end if;
  if d->'source'->>'type'='example' then
    if d->'source'->>'id' is distinct from '1ubq-colab-20261021-v1' then raise exception 'Invalid laboratory'; end if;
  elsif d->'source'->>'type'='archive' then
    if d->'source'->>'sha256' is distinct from encode(sha256(decode(d->'source'->>'base64','base64')),'hex') then raise exception 'Invalid laboratory'; end if;
  else raise exception 'Invalid laboratory'; end if;
  for entry in select * from jsonb_each(s->'drafts') loop
    n := length(entry.value->>'sequence');
    if entry.key !~ '^[a-f0-9]{64}$' or entry.value->>'sequence' !~ '^[ACDEFGHIKLMNPQRSTVWY]{3,120}$'
       or n is null or jsonb_typeof(entry.value->'protectedPositions') is distinct from 'array' then raise exception 'Invalid laboratory'; end if;
    for pos in select * from jsonb_array_elements(entry.value->'protectedPositions') loop
      if pos::text !~ '^(0|[1-9][0-9]{0,2})$' or pos::text::int >= n then raise exception 'Invalid laboratory'; end if;
    end loop;
    if (select count(*) <> count(distinct value) from jsonb_array_elements(entry.value->'protectedPositions')) then raise exception 'Invalid laboratory'; end if;
  end loop;
  if (s->>'selected')::int >= length(s->'drafts'->(s->>'activeId')->>'sequence') then raise exception 'Invalid laboratory'; end if;
exception when others then
  raise sqlstate 'PT422' using message='Invalid laboratory';
end;
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
     or p_workspace->>'schemaVersion' not in ('1', '2')
     or p_workspace->>'schemaVersion' is null
     or octet_length(p_workspace::text) > 1900000 then
    raise sqlstate 'PT422' using message = 'Invalid workspace';
  end if;
  perform public.pf_validate_laboratory(p_workspace);
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
    if exists(select 1 from public.analyses where project_id=p_id and type='workspace-v1' and results->>'schemaVersion'='2')
       and p_workspace->>'schemaVersion' <> '2' then
      raise sqlstate 'PT409' using message = 'Project requires current client';
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
    'hasLaboratory', coalesce(p_workspace->>'schemaVersion'='2' and p_workspace->'laboratory'->>'schemaVersion'='1', false),
    'laboratoryVariants', case when p_workspace->>'schemaVersion'='2' then public.pf_laboratory_variant_count(p_workspace) else 0 end,
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


revoke all on function public.pf_validate_laboratory(jsonb), public.pf_laboratory_variant_count(jsonb) from public, anon;
grant execute on function public.pf_validate_laboratory(jsonb), public.pf_laboratory_variant_count(jsonb) to authenticated;
notify pgrst, 'reload schema';
