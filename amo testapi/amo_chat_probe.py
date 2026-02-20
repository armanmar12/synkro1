import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ENV_PATH = os.path.join(BASE_DIR, ".env")
TOKENS_PATH = os.path.join(BASE_DIR, "tokens.json")
OUT_DIR = os.path.join(BASE_DIR, "chat_probe_out")


def load_env(path):
    if not os.path.exists(path):
        return
    with open(path, "r", encoding="utf-8") as f:
        for raw in f:
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            k = k.strip()
            v = v.strip().strip('"').strip("'")
            if k and v and k not in os.environ:
                os.environ[k] = v


def save_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def http_request(method, url, headers=None, data=None):
    req = urllib.request.Request(url, method=method)
    if headers:
        for k, v in headers.items():
            req.add_header(k, v)
    body = None
    if data is not None:
        body = json.dumps(data).encode("utf-8")
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, data=body, timeout=60) as resp:
            raw = resp.read().decode("utf-8")
            return resp.status, json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8")
        try:
            payload = json.loads(raw) if raw else {}
        except Exception:
            payload = {"raw": raw}
        return e.code, payload


def api_get(domain, access_token, path):
    url = f"https://{domain}{path}"
    headers = {"Authorization": f"Bearer {access_token}"}
    return http_request("GET", url, headers=headers)


def pick_lead_ids(leads_last24h_path, max_leads):
    if not os.path.exists(leads_last24h_path):
        return []
    with open(leads_last24h_path, "r", encoding="utf-8") as f:
        payload = json.load(f)
    leads = (payload.get("data") or {}).get("_embedded", {}).get("leads", []) or []
    out = []
    for lead in leads:
        if lead.get("id"):
            out.append(int(lead["id"]))
        if len(out) >= max_leads:
            break
    return out


def main():
    load_env(ENV_PATH)

    domain = os.environ.get("AMO_DOMAIN", "").strip()
    if not domain:
        print("Missing AMO_DOMAIN in .env")
        return

    if not os.path.exists(TOKENS_PATH):
        print("tokens.json not found, run OAuth first")
        return

    with open(TOKENS_PATH, "r", encoding="utf-8") as f:
        tokens = json.load(f)
    access_token = tokens.get("access_token", "").strip()
    if not access_token:
        print("access_token missing in tokens.json")
        return

    os.makedirs(OUT_DIR, exist_ok=True)

    # 1) Pick a few recent leads (deals)
    lead_ids = pick_lead_ids(os.path.join(BASE_DIR, "leads_last24h.json"), max_leads=3)

    # Also try to include leads that actually have talks.
    status, talks = api_get(domain, access_token, "/api/v4/talks?limit=10")
    save_json(os.path.join(OUT_DIR, "talks_sample.json"), {"status": status, "data": talks})
    if status == 200:
        for t in (talks.get("_embedded") or {}).get("talks", []) or []:
            if t.get("entity_type") == "lead" and t.get("entity_id"):
                lead_ids.append(int(t["entity_id"]))

    # de-dup but keep order
    dedup = []
    seen = set()
    for lid in lead_ids:
        if lid not in seen:
            seen.add(lid)
            dedup.append(lid)
    lead_ids = dedup[:5]

    if not lead_ids:
        print("No leads found (neither leads_last24h nor talks).")
        return

    summary = {"domain": domain, "ts": int(time.time()), "leads": []}

    # 2) For each lead, try to fetch: lead details, notes, talks, and possible chat endpoints
    for lead_id in lead_ids:
        lead_info = {"lead_id": lead_id, "calls": {}, "contact_ids": []}

        def call_and_save(key, path, out_name):
            status, data = api_get(domain, access_token, path)
            lead_info["calls"][key] = {"status": status, "path": path, "out": out_name}
            save_json(os.path.join(OUT_DIR, out_name), {"status": status, "data": data})
            return status, data

        # lead details with embedded contacts (deal is the central entity for you)
        _, lead = call_and_save(
            "lead",
            f"/api/v4/leads/{lead_id}?with=contacts",
            f"lead_{lead_id}.json",
        )
        contacts = (lead.get("_embedded") or {}).get("contacts") or []
        contact_ids = []
        for c in contacts:
            if c.get("id"):
                contact_ids.append(int(c["id"]))
        lead_info["contact_ids"] = contact_ids

        # notes on lead: often contains messages from communications
        call_and_save(
            "lead_notes",
            f"/api/v4/leads/{lead_id}/notes?limit=50",
            f"lead_{lead_id}_notes.json",
        )

        # Talks: in some accounts this is where chat threads are exposed and linked to leads.
        _, talks = call_and_save(
            "lead_talks",
            f"/api/v4/talks?filter[entity_type]=lead&filter[entity_id]={lead_id}&limit=10",
            f"lead_{lead_id}_talks.json",
        )
        first_talk_id = None
        try:
            first_talk_id = (talks.get("_embedded") or {}).get("talks", [])[0].get("talk_id")
        except Exception:
            first_talk_id = None
        if first_talk_id:
            # This usually requires extra scope; keep it as a probe to see if full dialog is accessible.
            call_and_save(
                "talk_messages",
                f"/api/v4/talks/{first_talk_id}/messages?limit=50",
                f"talk_{first_talk_id}_messages.json",
            )

        # Try plausible chat endpoints (may be unavailable depending on plan/scopes/features)
        call_and_save(
            "lead_chats_guess",
            f"/api/v4/leads/{lead_id}/chats?limit=50",
            f"lead_{lead_id}_chats_guess.json",
        )

        # Per-contact probes
        for contact_id in contact_ids[:2]:
            call_and_save(
                f"contact_{contact_id}",
                f"/api/v4/contacts/{contact_id}?with=leads",
                f"contact_{contact_id}.json",
            )
            call_and_save(
                f"contact_{contact_id}_notes",
                f"/api/v4/contacts/{contact_id}/notes?limit=50",
                f"contact_{contact_id}_notes.json",
            )
            call_and_save(
                f"contact_{contact_id}_chats_guess",
                f"/api/v4/contacts/{contact_id}/chats?limit=50",
                f"contact_{contact_id}_chats_guess.json",
            )

        summary["leads"].append(lead_info)

    # Global chat endpoints (if exist)
    status, _ = api_get(domain, access_token, "/api/v4/chats?limit=50")
    save_json(os.path.join(OUT_DIR, "chats_root.json"), {"status": status})
    summary["chats_root_status"] = status

    save_json(os.path.join(OUT_DIR, "summary.json"), summary)

    # Print a small, greppable summary
    print("Leads:", ", ".join(str(x) for x in lead_ids))
    print("Saved to:", OUT_DIR)
    for lead in summary["leads"]:
        lead_id = lead["lead_id"]
        s1 = lead["calls"].get("lead_notes", {}).get("status")
        s2 = lead["calls"].get("lead_chats_guess", {}).get("status")
        print(f"lead {lead_id}: notes={s1} chats_guess={s2} contacts={lead.get('contact_ids')}")
    print("chats_root status:", summary.get("chats_root_status"))


if __name__ == "__main__":
    main()
