-- Earlier versions compared with the current content: each one says whether its sequence is the same as
-- the one stored now, compared in full here because a version's preview carries only a prefix.
--
-- A new function rather than a new key in pf_list_project_versions: the published API refuses unknown
-- fields in a version, so applying this before the new API must not break the current one.
-- Owner-scoped like pf_list_project_versions (invoker rights, RLS on both tables). Idempotent.

create or replace function public.pf_list_project_versions_compared(p_id uuid, p_prefix_length integer)
returns jsonb language sql stable security invoker set search_path = '' as $$
  select coalesce(jsonb_agg(jsonb_build_object(
      'id', v.id, 'savedAt', v.saved_at,
      'preview', public.pf_project_preview(v.active_sequence, v.workspace, p_prefix_length),
      'sameSequence', v.active_sequence = p.active_sequence
    ) order by v.saved_at desc, v.id desc), '[]'::jsonb)
    from public.project_versions v
    join public.projects p on p.id = v.project_id
    where v.project_id = p_id and v.owner_id = (select auth.uid());
$$;

revoke all on function public.pf_list_project_versions_compared(uuid,integer) from public, anon;
grant execute on function public.pf_list_project_versions_compared(uuid,integer) to authenticated;
notify pgrst, 'reload schema';
