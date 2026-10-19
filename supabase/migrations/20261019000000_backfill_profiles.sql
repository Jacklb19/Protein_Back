-- Profiles for accounts created before the signup trigger existed.
-- Projects reference public.profiles; without this row saving fails with 23503 (foreign key).
-- Idempotent: accounts that already have a profile are left unchanged.
insert into public.profiles (id, display_name, preferences)
select
  users.id,
  coalesce(users.raw_user_meta_data->>'full_name', users.raw_user_meta_data->>'name', ''),
  '{}'::jsonb
from auth.users as users
on conflict (id) do nothing;
