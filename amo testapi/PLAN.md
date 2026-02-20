# amoCRM + Radist + Supabase Plan

This plan reflects the current decisions:
- amoCRM provides deal metadata, responsible, stages, and mapping to contacts (read-only; no writes back).
- Radist provides full dialog text.
- Analysis runs per shift window (UTC+5, 22:00 → 22:00).

## Phase 0 ? Local setup (completed)
- [x] Install ngrok
- [x] Start ngrok HTTP tunnel
- [x] Copy the public HTTPS URL from ngrok

## Phase 1 ? Create amoCRM External Integration (completed)
- [x] Create external integration
- [x] Set Redirect URL and Webhook URL
- [x] Enable scopes: account data (+ notifications optional)
- [x] Save integration

## Phase 2 ? Local OAuth receiver + token capture (completed)
- [x] Run local OAuth receiver
- [x] Save values to `.env`
- [x] Exchange code for tokens
- [x] Save `tokens.json`

## Phase 3 ? Verify amoCRM data access (completed)
- [x] Account, pipelines/statuses, users, leads
- [x] Export raw JSON files
- [x] Confirm chats/messages not accessible via CRM API

## Phase 4 ? Decide chat source (completed)
- [x] Use Radist API for full dialogs
- [x] Use amoCRM for deal metadata + phone mapping

## Phase 5 ? Data model (Supabase)
- [ ] Define `tenants` table
- [x] Define `deals` table (deal metadata + shift window)
- [ ] Define `messages` table (per message, dedup by `message_id`)
- [ ] Define `raw_payloads` table (optional for debugging)
- [x] Agree on JSON columns for `deal_attrs` and `contact_attrs`
- [x] Define `reports` table (daily/weekly/monthly text)

## Phase 6 ? Shift window logic
- [ ] Set timezone: `UTC+5`
- [ ] Set shift start: `22:00`
- [ ] Compute window as `[start, end)` using last fully closed shift
- [ ] Add lookback option (48–72h) for Radist catch-up

## Phase 7 ? ETL pipeline (amo -> Supabase -> Radist)
- [ ] amo (read-only): fetch leads updated in window and stage filter (optional)
- [ ] amo (read-only): fetch linked contacts via `/leads/{id}/links` and all phones
- [ ] Map deal -> phone (normalized) and responsible
- [ ] Upsert into `deals_daily`
- [ ] Radist: fetch chats, map by phone, fetch messages by chat_id
- [ ] Filter messages in window, dedup by `message_id`
- [ ] Update `deals_daily.dialog_raw` and insert into `messages`

## Phase 8 ? Analysis pipeline
- [ ] Run analysis only after ETL success
- [ ] Build prompts per deal/manager per shift
- [ ] Store analysis results

## Phase 9 ? Client portal (MVP)
- [ ] Basic backend API
- [ ] Login / tenant isolation
- [ ] Daily report view
- [ ] Manager performance breakdown

## Phase 10 ? Production
- [ ] Replace ngrok with real server
- [ ] Configure cron/worker
- [ ] Logging + monitoring
- [ ] Backup strategy

