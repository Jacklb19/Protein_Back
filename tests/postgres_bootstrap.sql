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
