# amoCRM test API (external/private)

This folder has two scripts:
- `local_oauth_server.py` - local callback/webhook receiver for integration setup
- `amo_probe.py` - API probe after tokens are saved

Important: Synkro's amoCRM integration is read-only. We never write/update anything in amoCRM
(no pipeline moves, no notes/tasks, no field edits). We only read/copy the data we need.

## 1) Create `.env`
Copy `.env.example` to `.env` and fill:
- `AMO_DOMAIN` (for example `globalfruit.amocrm.ru`)
- `AMO_LOCAL_HOST` and `AMO_LOCAL_PORT` (default `127.0.0.1:8787`)

`AMO_CLIENT_ID`, `AMO_CLIENT_SECRET`, and `AMO_REDIRECT_URI` can be filled now or later.

## 2) Start local OAuth receiver
```powershell
python .\local_oauth_server.py
```

## 3) Start ngrok tunnel to local receiver
```powershell
ngrok http 8787
```
Use your actual `AMO_LOCAL_PORT` if not `8787`.

## 4) Create External integration in amoCRM
Set:
- Redirect URL: `https://<ngrok-id>.ngrok-free.app/amocrm/callback`
- Webhook URL: `https://<ngrok-id>.ngrok-free.app/amocrm/webhook`
- Scopes (minimum): account data access

After install/authorize:
- callback will save `AMO_AUTH_CODE` to `.env`
- webhook payload will be saved to `webhook_last.json`
- token exchange result will be saved to `tokens.json`

## 5) Probe amoCRM API
```powershell
python .\amo_probe.py
```

Outputs:
- `tokens.json`
- `account.json`
- `pipelines.json`
- `users.json` (if allowed)
- `leads_last24h.json`
