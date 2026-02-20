-- Synkro: initial Supabase schema
-- 1 row = 1 deal. Dialog is stored as raw JSON + normalized human-readable text.

create table if not exists public.deals (
  tenant_id           text        not null,
  deal_id             bigint      not null,
  deal_name           text        not null,

  status_id           bigint      null,
  status              text        null,
  responsible         text        null,  -- store full email/login as-is
  phone               text        null,  -- normalized digits-only (e.g. 7701...)
  chat_id             bigint      null,

  first_message_at    timestamptz null,
  last_message_at     timestamptz null,
  messages_count      integer     not null default 0,

  deal_attrs_json     jsonb       not null default '{}'::jsonb,
  contact_attrs_json  jsonb       not null default '{}'::jsonb,

  dialog_raw          jsonb       null,  -- raw Radist payload (messages)
  dialog_norm         text        null,  -- normalized "WhatsApp-like" transcript (+05:00, seconds precision)

  comment             text        null,

  inserted_at         timestamptz not null default now(),
  updated_at          timestamptz not null default now(),

  primary key (tenant_id, deal_id)
);

create index if not exists deals_last_message_at_idx on public.deals (tenant_id, last_message_at desc);
create index if not exists deals_phone_idx on public.deals (tenant_id, phone);
create index if not exists deals_chat_id_idx on public.deals (tenant_id, chat_id);

create or replace function public.set_updated_at()
returns trigger
language plpgsql
as $$
begin
  new.updated_at = now();
  return new;
end;
$$;

drop trigger if exists deals_set_updated_at on public.deals;
create trigger deals_set_updated_at
before update on public.deals
for each row
execute function public.set_updated_at();

do $$
begin
  if not exists (select 1 from pg_type where typname = 'report_type') then
    create type public.report_type as enum ('daily', 'weekly', 'monthly');
  end if;
end$$;

create table if not exists public.reports (
  id          bigserial primary key,
  tenant_id   text not null,
  report_date date not null,
  type        public.report_type not null,
  text        text not null,
  comment     text null,
  created_at  timestamptz not null default now()
);

create index if not exists reports_lookup_idx on public.reports (tenant_id, report_date desc, type);

