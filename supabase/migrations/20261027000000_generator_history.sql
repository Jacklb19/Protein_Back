-- Generator history inside the existing workspace snapshot (workspace schemaVersion 3).
-- No new tables, columns, policies or storage. Existing rows are not changed; reapplying is safe.

create or replace function public.pf_generator_run_count(w jsonb)
returns integer language plpgsql immutable security invoker set search_path='' as $$
begin
  return least(10, jsonb_array_length((w->'generatorHistory'->>'documentJson')::jsonb->'runs'));
exception when others then return 0;
end;
$$;

-- Shape only, mirroring app/generator_history.py; candidates are never recalculated here.
create or replace function public.pf_validate_generator_history(w jsonb)
returns void language plpgsql immutable security invoker set search_path='' as $$
declare h jsonb; d jsonb; run jsonb; r jsonb; p jsonb; arm text; candidate jsonb; n integer; counts jsonb;
begin
  h := w->'generatorHistory';
  if w->>'schemaVersion' in ('1', '2') then
    if h is not null and h <> 'null'::jsonb then raise exception 'Invalid generator history'; end if;
    return;
  end if;
  if h is null or h = 'null'::jsonb then return; end if;
  if jsonb_typeof(h) is distinct from 'object'
     or (select count(*) from jsonb_object_keys(h)) <> 3
     or h->>'schemaVersion' is distinct from '1' or jsonb_typeof(h->'schemaVersion') is distinct from 'number'
     or jsonb_typeof(h->'documentJson') is distinct from 'string'
     or octet_length(h->>'documentJson') > 100000
     or h->>'documentSha256' is distinct from encode(sha256(convert_to(h->>'documentJson','UTF8')),'hex') then
    raise exception 'Invalid generator history';
  end if;
  d := (h->>'documentJson')::jsonb;
  if jsonb_typeof(d) is distinct from 'object' or (select count(*) from jsonb_object_keys(d)) <> 3
     or d->>'kind' is distinct from 'proteinforge-generator-history'
     or d->>'schemaVersion' is distinct from '1' or jsonb_typeof(d->'schemaVersion') is distinct from 'number'
     or jsonb_typeof(d->'runs') is distinct from 'array'
     or jsonb_array_length(d->'runs') not between 1 and 10
     or (select count(distinct value->>'id') from jsonb_array_elements(d->'runs')) <> jsonb_array_length(d->'runs') then
    raise exception 'Invalid generator history';
  end if;
  for run in select value from jsonb_array_elements(d->'runs') loop
    if jsonb_typeof(run) is distinct from 'object'
       or (select array_agg(k order by k) from jsonb_object_keys(run) k) is distinct from array['appBuild','id','origin','recordedAt','resultJson','selection']
       or run->>'origin' is distinct from 'local-generator'
       or jsonb_typeof(run->'resultJson') is distinct from 'string'
       or octet_length(run->>'resultJson') > 10000
       or run->>'id' is distinct from encode(sha256(convert_to(run->>'resultJson','UTF8')),'hex')
       or jsonb_typeof(run->'recordedAt') is distinct from 'string'
       or length(run->>'recordedAt') > 64
       or run->>'recordedAt' !~ '(Z|[+-][0-9]{2}:[0-9]{2})$'
       or (run->>'recordedAt')::timestamptz is null
       or not (jsonb_typeof(run->'appBuild') = 'null' or (jsonb_typeof(run->'appBuild') = 'string' and run->>'appBuild' ~ '^[0-9a-f]{7,40}$')) then
      raise exception 'Invalid generator history';
    end if;
    r := (run->>'resultJson')::jsonb;
    p := r->'parameters';
    -- A missing field yields NULL; coalesce treats it as invalid instead of skipping the check.
    if coalesce(jsonb_typeof(r) is distinct from 'object'
       or (select array_agg(k order by k) from jsonb_object_keys(r) k) is distinct from array['guided','methodVersion','parameters','reference','references','schemaVersion']
       or r->>'schemaVersion' is distinct from '1' or jsonb_typeof(r->'schemaVersion') is distinct from 'number'
       or r->>'methodVersion' is distinct from 'proteinforge-candidates-v1'
       or jsonb_typeof(p) is distinct from 'object'
       or p->>'length' !~ '^[0-9]{1,3}$' or (p->>'length')::int not between 5 and 300
       or p->>'budget' !~ '^[0-9]{1,4}$' or (p->>'budget')::int not between 100 and 5000
       or p->>'seed' !~ '^[0-9]{1,10}$' or (p->>'seed')::bigint not between 1 and 4294967295, true) then
      raise exception 'Invalid generator history';
    end if;
    counts := '{}'::jsonb;
    foreach arm in array array['guided','reference'] loop
      if jsonb_typeof(r->arm->'candidates') is distinct from 'array'
         or jsonb_array_length(r->arm->'candidates') not between 1 and 5 then
        raise exception 'Invalid generator history';
      end if;
      for candidate in select value from jsonb_array_elements(r->arm->'candidates') loop
        n := length(candidate->>'sequence');
        if coalesce(candidate->>'sequence' !~ '^[ACDEFGHIKLMNPQRSTVWY]+$' or n <> (p->>'length')::int
           or candidate->'descriptors'->>'length' is distinct from n::text, true) then
          raise exception 'Invalid generator history';
        end if;
      end loop;
      counts := counts || jsonb_build_object(arm, jsonb_array_length(r->arm->'candidates'));
    end loop;
    if jsonb_typeof(run->'selection') = 'object' then
      if coalesce((select count(*) from jsonb_object_keys(run->'selection')) <> 2
         or run->'selection'->>'arm' not in ('guided','reference')
         or run->'selection'->>'index' !~ '^[0-9]$'
         or (run->'selection'->>'index')::int >= (counts->>(run->'selection'->>'arm'))::int, true) then
        raise exception 'Invalid generator history';
      end if;
    elsif jsonb_typeof(run->'selection') is distinct from 'null' then
      raise exception 'Invalid generator history';
    end if;
  end loop;
exception when others then
  raise sqlstate 'PT422' using message='Invalid generator history';
end;
$$;

-- Same checks as 20261021000000, except that version 3 makes the laboratory optional.
create or replace function public.pf_validate_laboratory(w jsonb)
returns void language plpgsql immutable security invoker set search_path='' as $$
declare l jsonb; d jsonb; s jsonb; entry record; pos jsonb; n integer;
begin
  l := w->'laboratory';
  if w->>'schemaVersion'='1' then
    if l is not null and l <> 'null'::jsonb then raise exception 'Invalid laboratory'; end if;
    return;
  end if;
  if w->>'schemaVersion'='3' and (l is null or l = 'null'::jsonb) then return; end if;
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
  stored_version text;
begin
  if auth.uid() is null then raise sqlstate 'PT401' using message = 'Authentication required'; end if;
  if p_name is null or length(btrim(p_name)) not between 1 and 120
     or p_sequence is null or length(p_sequence) > 100000
     or p_sequence !~ '^[ACDEFGHIKLMNPQRSTVWYUOBZX]*$'
     or jsonb_typeof(p_workspace) is distinct from 'object'
     or p_workspace->>'schemaVersion' not in ('1', '2', '3')
     or p_workspace->>'schemaVersion' is null
     or jsonb_typeof(p_workspace->'schemaVersion') is distinct from 'number'
     or octet_length(p_workspace::text) > 1900000 then
    raise sqlstate 'PT422' using message = 'Invalid workspace';
  end if;
  perform public.pf_validate_laboratory(p_workspace);
  perform public.pf_validate_generator_history(p_workspace);
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
    -- Monotonic guard: a client never replaces a snapshot version it does not understand.
    select results->>'schemaVersion' into stored_version from public.analyses
      where project_id = p_id and type = 'workspace-v1';
    if stored_version ~ '^[0-9]{1,3}$' and stored_version::int > (p_workspace->>'schemaVersion')::int then
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

-- Compact projection only, as in 20261021000000, plus the generator run count. No documents.
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
        end as alignment_matches,
      coalesce(p_workspace->>'schemaVersion' in ('2', '3') and p_workspace->'laboratory'->>'schemaVersion' = '1', false) as has_laboratory
    from evidence
  )
  select jsonb_build_object(
    'hasLaboratory', has_laboratory,
    'laboratoryVariants', case when has_laboratory then public.pf_laboratory_variant_count(p_workspace) else 0 end,
    'generatorRuns', case when p_workspace->>'schemaVersion' = '3' and p_workspace->'generatorHistory'->>'schemaVersion' = '1' then public.pf_generator_run_count(p_workspace) else 0 end,
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

revoke all on function public.pf_validate_generator_history(jsonb), public.pf_generator_run_count(jsonb) from public, anon;
grant execute on function public.pf_validate_generator_history(jsonb), public.pf_generator_run_count(jsonb) to authenticated;
notify pgrst, 'reload schema';
