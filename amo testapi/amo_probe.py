import json
import os
import time
import urllib.request
import urllib.error

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ENV_PATH = os.path.join(BASE_DIR, '.env')
TOKENS_PATH = os.path.join(BASE_DIR, 'tokens.json')


def load_env(path):
    if not os.path.exists(path):
        return
    with open(path, 'r', encoding='utf-8') as f:
        for raw in f:
            line = raw.strip()
            if not line or line.startswith('#'):
                continue
            if '=' not in line:
                continue
            k, v = line.split('=', 1)
            k = k.strip()
            v = v.strip().strip('"').strip("'")
            if k and v and k not in os.environ:
                os.environ[k] = v


def save_json(path, data):
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def http_request(method, url, headers=None, data=None):
    req = urllib.request.Request(url, method=method)
    if headers:
        for k, v in headers.items():
            req.add_header(k, v)
    body = None
    if data is not None:
        body = json.dumps(data).encode('utf-8')
        req.add_header('Content-Type', 'application/json')
    try:
        with urllib.request.urlopen(req, data=body, timeout=60) as resp:
            raw = resp.read().decode('utf-8')
            return resp.status, json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        raw = e.read().decode('utf-8')
        try:
            payload = json.loads(raw) if raw else {}
        except Exception:
            payload = {'raw': raw}
        return e.code, payload


def oauth_exchange(domain, client_id, client_secret, redirect_uri, code):
    url = f'https://{domain}/oauth2/access_token'
    payload = {
        'client_id': client_id,
        'client_secret': client_secret,
        'grant_type': 'authorization_code',
        'code': code,
        'redirect_uri': redirect_uri,
    }
    return http_request('POST', url, data=payload)


def oauth_refresh(domain, client_id, client_secret, redirect_uri, refresh_token):
    url = f'https://{domain}/oauth2/access_token'
    payload = {
        'client_id': client_id,
        'client_secret': client_secret,
        'grant_type': 'refresh_token',
        'refresh_token': refresh_token,
        'redirect_uri': redirect_uri,
    }
    return http_request('POST', url, data=payload)


def api_get(domain, access_token, path):
    url = f'https://{domain}{path}'
    headers = {
        'Authorization': f'Bearer {access_token}',
    }
    return http_request('GET', url, headers=headers)


def main():
    load_env(ENV_PATH)

    domain = os.environ.get('AMO_DOMAIN', '').strip()
    client_id = os.environ.get('AMO_CLIENT_ID', '').strip()
    client_secret = os.environ.get('AMO_CLIENT_SECRET', '').strip()
    redirect_uri = os.environ.get('AMO_REDIRECT_URI', '').strip()
    auth_code = os.environ.get('AMO_AUTH_CODE', '').strip()

    tokens = {}
    if os.path.exists(TOKENS_PATH):
        with open(TOKENS_PATH, 'r', encoding='utf-8') as f:
            tokens = json.load(f)

    access_token = os.environ.get('AMO_ACCESS_TOKEN', '').strip() or tokens.get('access_token', '')
    refresh_token = os.environ.get('AMO_REFRESH_TOKEN', '').strip() or tokens.get('refresh_token', '')

    if not domain or not client_id or not client_secret or not redirect_uri:
        print('Missing required env vars: AMO_DOMAIN, AMO_CLIENT_ID, AMO_CLIENT_SECRET, AMO_REDIRECT_URI')
        return

    # Step 1: get tokens
    if not access_token:
        if auth_code:
            status, data = oauth_exchange(domain, client_id, client_secret, redirect_uri, auth_code)
            if status != 200:
                print('OAuth exchange failed:', status, data)
                return
            tokens = data
        elif refresh_token:
            status, data = oauth_refresh(domain, client_id, client_secret, redirect_uri, refresh_token)
            if status != 200:
                print('OAuth refresh failed:', status, data)
                return
            tokens = data
        else:
            print('No access token. Provide AMO_AUTH_CODE or AMO_REFRESH_TOKEN in .env')
            return

        save_json(TOKENS_PATH, tokens)
        access_token = tokens.get('access_token', '')
        refresh_token = tokens.get('refresh_token', '')

    if not access_token:
        print('Access token missing after OAuth flow')
        return

    # Step 2: basic probes
    # This account rejects any "with" query for /api/v4/account (returns 400 Invalid with).
    status, account = api_get(domain, access_token, '/api/v4/account')
    save_json(os.path.join(BASE_DIR, 'account.json'), {'status': status, 'data': account})

    status, pipelines = api_get(domain, access_token, '/api/v4/leads/pipelines')
    save_json(os.path.join(BASE_DIR, 'pipelines.json'), {'status': status, 'data': pipelines})

    # Users endpoint may be blocked for external integrations
    status, users = api_get(domain, access_token, '/api/v4/users')
    save_json(os.path.join(BASE_DIR, 'users.json'), {'status': status, 'data': users})

    # Leads updated in last 24h
    now = int(time.time())
    from_ts = now - 24 * 3600
    leads_path = f'/api/v4/leads?filter[updated_at][from]={from_ts}&limit=50'
    status, leads = api_get(domain, access_token, leads_path)
    save_json(os.path.join(BASE_DIR, 'leads_last24h.json'), {'status': status, 'data': leads})

    print('Done. Check JSON files in this folder.')


if __name__ == '__main__':
    main()
