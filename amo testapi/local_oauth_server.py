import json
import os
import secrets
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ENV_PATH = os.path.join(BASE_DIR, ".env")
TOKENS_PATH = os.path.join(BASE_DIR, "tokens.json")
WEBHOOK_DUMP_PATH = os.path.join(BASE_DIR, "webhook_last.json")


def load_env(path):
    if not os.path.exists(path):
        return {}
    env = {}
    with open(path, "r", encoding="utf-8") as f:
        for raw in f:
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            k = k.strip()
            v = v.strip().strip('"').strip("'")
            if k:
                env[k] = v
    return env


def set_env_values(path, updates):
    existing = {}
    lines = []

    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            lines = f.readlines()
        for line in lines:
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                continue
            k, v = stripped.split("=", 1)
            existing[k.strip()] = v.strip().strip('"').strip("'")

    existing.update({k: v for k, v in updates.items() if v is not None and v != ""})

    out_lines = []
    seen = set()
    for line in lines:
        stripped = line.strip()
        if "=" in stripped and not stripped.startswith("#"):
            k = stripped.split("=", 1)[0].strip()
            if k in existing:
                out_lines.append(f"{k}={existing[k]}\n")
                seen.add(k)
            else:
                out_lines.append(line)
        else:
            out_lines.append(line)

    for k, v in existing.items():
        if k not in seen:
            out_lines.append(f"{k}={v}\n")

    with open(path, "w", encoding="utf-8") as f:
        f.writelines(out_lines)


def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def http_json(method, url, payload):
    req = urllib.request.Request(url, method=method)
    req.add_header("Content-Type", "application/json")
    body = json.dumps(payload).encode("utf-8")
    with urllib.request.urlopen(req, data=body, timeout=30) as resp:
        raw = resp.read().decode("utf-8")
        return resp.status, json.loads(raw) if raw else {}


def exchange_code_for_tokens(env, code):
    domain = env.get("AMO_DOMAIN", "").strip()
    client_id = env.get("AMO_CLIENT_ID", "").strip()
    client_secret = env.get("AMO_CLIENT_SECRET", "").strip()
    redirect_uri = env.get("AMO_REDIRECT_URI", "").strip()

    if not (domain and client_id and client_secret and redirect_uri and code):
        missing = []
        for k in ("AMO_DOMAIN", "AMO_CLIENT_ID", "AMO_CLIENT_SECRET", "AMO_REDIRECT_URI"):
            if not env.get(k, "").strip():
                missing.append(k)
        if not code:
            missing.append("code")
        return False, "Missing: " + ", ".join(missing)

    url = f"https://{domain}/oauth2/access_token"
    payload = {
        "client_id": client_id,
        "client_secret": client_secret,
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri,
    }
    try:
        status, data = http_json("POST", url, payload)
        if status != 200:
            return False, f"Unexpected status {status}: {data}"
        save_json(TOKENS_PATH, data)
        updates = {
            "AMO_ACCESS_TOKEN": data.get("access_token", ""),
            "AMO_REFRESH_TOKEN": data.get("refresh_token", ""),
            # Auth code is one-time; clear it after successful exchange.
            "AMO_AUTH_CODE": "",
        }
        set_env_values(ENV_PATH, updates)
        return True, "Token exchange OK, saved to tokens.json and .env"
    except Exception as e:
        return False, f"Exchange failed: {e}"


class Handler(BaseHTTPRequestHandler):
    def _reply(self, status, content_type, body):
        data = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/health":
            self._reply(200, "application/json; charset=utf-8", json.dumps({"ok": True}))
            return

        # Convenience endpoint: starts OAuth by redirecting to amoCRM's consent page.
        # Works well when you don't see an "approve access" button in the UI.
        if parsed.path == "/amocrm/start":
            env = load_env(ENV_PATH)
            client_id = env.get("AMO_CLIENT_ID", "").strip()
            if not client_id:
                self._reply(
                    400,
                    "application/json; charset=utf-8",
                    json.dumps({"error": "missing_AMO_CLIENT_ID"}),
                )
                return

            state = secrets.token_urlsafe(16)
            set_env_values(ENV_PATH, {"AMO_OAUTH_STATE": state})

            qs = urllib.parse.urlencode({"client_id": client_id, "state": state, "mode": "popup"})
            auth_url = f"https://www.amocrm.ru/oauth?{qs}"
            self.send_response(302)
            self.send_header("Location", auth_url)
            self.end_headers()
            return

        if parsed.path == "/amocrm/callback":
            query = urllib.parse.parse_qs(parsed.query or "")
            code = (query.get("code") or [""])[0]
            state = (query.get("state") or [""])[0]
            referer = self.headers.get("Referer", "")
            updates = {"AMO_AUTH_CODE": code}
            if referer:
                updates["AMO_LAST_REFERER"] = referer
            if state:
                updates["AMO_OAUTH_STATE_RCVD"] = state
            set_env_values(ENV_PATH, updates)

            env = load_env(ENV_PATH)
            ok, msg = exchange_code_for_tokens(env, code) if code else (False, "No code in query")
            payload = {
                "path": parsed.path,
                "code_received": bool(code),
                "token_exchange_ok": ok,
                "message": msg,
            }
            html = (
                "<html><body><h2>amoCRM callback received</h2>"
                f"<pre>{json.dumps(payload, ensure_ascii=False, indent=2)}</pre>"
                "</body></html>"
            )
            self._reply(200, "text/html; charset=utf-8", html)
            return

        self._reply(404, "application/json; charset=utf-8", json.dumps({"error": "not_found"}))

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path != "/amocrm/webhook":
            self._reply(404, "application/json; charset=utf-8", json.dumps({"error": "not_found"}))
            return

        length = int(self.headers.get("Content-Length", "0") or "0")
        body_raw = self.rfile.read(length) if length > 0 else b""
        body_text = body_raw.decode("utf-8", errors="replace")
        ctype = self.headers.get("Content-Type", "")

        parsed_data = {}
        if "application/json" in ctype:
            try:
                parsed_data = json.loads(body_text) if body_text else {}
            except Exception:
                parsed_data = {"_raw": body_text}
        else:
            form = urllib.parse.parse_qs(body_text, keep_blank_values=True)
            parsed_data = {k: (v[0] if len(v) == 1 else v) for k, v in form.items()}

        save_json(
            WEBHOOK_DUMP_PATH,
            {
                "path": parsed.path,
                "content_type": ctype,
                "headers": dict(self.headers.items()),
                "body_text": body_text,
                "parsed": parsed_data,
            },
        )

        updates = {
            "AMO_CLIENT_ID": parsed_data.get("client_id", ""),
            "AMO_CLIENT_SECRET": parsed_data.get("client_secret", ""),
        }
        set_env_values(ENV_PATH, updates)

        self._reply(200, "application/json; charset=utf-8", json.dumps({"ok": True}))


def main():
    env = load_env(ENV_PATH)
    port = int(env.get("AMO_LOCAL_PORT", "8787"))
    host = env.get("AMO_LOCAL_HOST", "127.0.0.1")
    server = ThreadingHTTPServer((host, port), Handler)
    print(f"Listening on http://{host}:{port}")
    print("Callback: /amocrm/callback")
    print("Webhook:  /amocrm/webhook")
    print("Health:   /health")
    server.serve_forever()


if __name__ == "__main__":
    main()
