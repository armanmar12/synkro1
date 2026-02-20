# Synkro scope / invariants

## amoCRM integration: read-only

We do not change anything inside amoCRM.

We only copy/read the data we need for work and analytics:
- deals/leads metadata (id, name, pipeline/stage, responsible, timestamps)
- links to contacts + phones (for matching dialogs)
- other reference data that is safe to read (pipelines, users, etc.)

Explicitly out of scope (no writes to amoCRM):
- creating/updating leads, contacts, companies
- moving deals across pipelines/stages
- writing notes/comments, tasks, tags
- editing custom fields, budgets, dates, responsible users
- any automations that push data back into amoCRM

Allowed technical exceptions (still not business writes):
- OAuth token exchange/refresh (HTTP POST to amoCRM OAuth endpoint)
- receiving amoCRM webhooks/notifications (inbound only)

