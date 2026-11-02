-- Only for a fresh disposable PostgreSQL cluster. Not a production migration.
create role anon nologin;
create role authenticated nologin;
create schema auth;
create extension if not exists pgcrypto;
create table auth.users (id uuid primary key, raw_user_meta_data jsonb default '{}'::jsonb);
create function auth.uid() returns uuid language sql stable as $$
  select nullif(current_setting('request.jwt.claim.sub', true), '')::uuid;
$$;
grant usage on schema auth, public to anon, authenticated;
grant execute on function auth.uid() to anon, authenticated;
-- Minimal emulation of the Supabase Storage schema: the tables and helper that report policies use.
-- The Storage API itself (signed URLs, uploads, deletions) is not emulated.
create schema storage;
create table storage.buckets (
  id text primary key, name text not null unique, public boolean default false,
  file_size_limit bigint, allowed_mime_types text[], created_at timestamptz default now()
);
create table storage.objects (
  id uuid primary key default gen_random_uuid(), bucket_id text references storage.buckets(id),
  name text not null, owner uuid, metadata jsonb, created_at timestamptz default now(), unique (bucket_id, name)
);
alter table storage.buckets enable row level security;
alter table storage.objects enable row level security;
create function storage.foldername(name text) returns text[] language sql immutable as $$
  select (string_to_array(name, '/'))[1:array_length(string_to_array(name, '/'), 1) - 1];
$$;
grant usage on schema storage to anon, authenticated;
grant select, insert, update, delete on storage.objects to anon, authenticated;
grant select on storage.buckets to anon, authenticated;
grant execute on function storage.foldername(text) to anon, authenticated;
