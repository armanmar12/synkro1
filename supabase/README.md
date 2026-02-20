# Supabase

Apply migrations:
- In Supabase Dashboard: SQL Editor -> run `supabase/migrations/001_init.sql`
- Or via Supabase CLI (if you use it): `supabase db push`

Security note:
- Never commit or paste `sb_secret_*` keys into chat or repo.
- Use service role key only on backend servers (in `.env`), never in browsers.
- For PostgREST `/rest/v1/*` access you typically need the legacy JWT keys (`eyJ...`) for `Authorization: Bearer ...`.
