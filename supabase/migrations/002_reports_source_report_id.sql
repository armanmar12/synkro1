-- Ensure each app report can be stored as its own row in Supabase reports table.
-- This allows multiple reports on the same day without conflicts.

alter table if exists public.reports
  add column if not exists source_report_id bigint null;

create unique index if not exists reports_source_report_uidx
  on public.reports (tenant_id, source_report_id)
  where source_report_id is not null;
