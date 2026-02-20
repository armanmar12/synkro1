import hashlib
import json
import time
from datetime import datetime, timedelta, timezone as dt_timezone
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


class ConnectorError(Exception):
    pass


def sync_sources_to_supabase(
    *,
    tenant_slug: str,
    mode: str,
    window_start: datetime,
    window_end: datetime,
    runtime_config,
    supabase_public: dict,
    supabase_secret: dict,
    amocrm_public: dict | None,
    amocrm_secret: dict | None,
    radist_public: dict | None,
    radist_secret: dict | None,
) -> dict:
    supabase_url = (supabase_public or {}).get("url", "").rstrip("/")
    service_key = (supabase_secret or {}).get("service_role_key") or (
        supabase_secret or {}
    ).get("service_role_jwt")
    if not supabase_url or not service_key:
        raise ConnectorError("Supabase credentials are incomplete.")

    radist_fetch_limit = max(int(getattr(runtime_config, "radist_fetch_limit", 200) or 200), 10)
    runtime_meta = getattr(runtime_config, "metadata", {}) or {}
    max_amo_leads = _bounded_int(runtime_meta.get("max_amo_leads"), 500, 50, 5000)
    max_amo_contacts = _bounded_int(runtime_meta.get("max_amo_contacts"), 800, 50, 5000)
    max_radist_contact_pages = _bounded_int(
        runtime_meta.get("max_radist_contact_pages"), 25, 1, 200
    )
    max_radist_candidates = _bounded_int(
        runtime_meta.get("max_radist_candidates"), max(radist_fetch_limit * 3, 300), 50, 3000
    )
    max_radist_message_pages = _bounded_int(
        runtime_meta.get("max_radist_message_pages"), 10, 1, 50
    )

    amo_rows = []
    radist_dialogs = []
    if mode in {"amocrm_radist", "amocrm_only"}:
        amo_rows = _collect_amo_rows(
            amocrm_public or {},
            amocrm_secret or {},
            window_start=window_start,
            window_end=window_end,
            max_leads=max_amo_leads,
            max_contacts=max_amo_contacts,
        )
    if mode in {"amocrm_radist", "radist_only"}:
        target_phones = None
        if mode == "amocrm_radist":
            target_phones = {
                phone
                for row in amo_rows
                for phone in row.get("_phones", [])
                if phone
            }
        radist_dialogs = _collect_radist_dialogs(
            radist_public or {},
            radist_secret or {},
            window_start=window_start,
            window_end=window_end,
            fetch_limit=radist_fetch_limit,
            target_phones=target_phones,
            max_contact_pages=max_radist_contact_pages,
            max_candidates=max_radist_candidates,
            max_message_pages=max_radist_message_pages,
        )

    rows = _merge_rows(
        tenant_slug=tenant_slug,
        mode=mode,
        amo_rows=amo_rows,
        radist_dialogs=radist_dialogs,
    )
    if rows:
        _supabase_upsert_deals(supabase_url, service_key, rows)

    return {
        "mode": mode,
        "amo_rows": len(amo_rows),
        "radist_dialogs": len(radist_dialogs),
        "upsert_rows": len(rows),
    }


def _collect_amo_rows(
    amocrm_public: dict,
    amocrm_secret: dict,
    *,
    window_start: datetime,
    window_end: datetime,
    max_leads: int,
    max_contacts: int,
) -> list[dict]:
    domain = (amocrm_public.get("domain") or "").strip()
    token = (amocrm_secret.get("access_token") or "").strip()
    if not domain or not token:
        raise ConnectorError("amoCRM domain/token is not configured.")

    base = domain if domain.startswith("http") else f"https://{domain}"
    base = base.rstrip("/")
    status_map = _amo_status_map(base, token)
    leads = _amo_fetch_leads(base, token, window_start, window_end, max_leads=max_leads)

    contact_ids = {
        _to_int(contact.get("id"))
        for lead in leads
        for contact in ((lead.get("_embedded") or {}).get("contacts") or [])
        if contact.get("id")
    }
    contact_ids.discard(0)
    contacts_map = _amo_fetch_contacts(base, token, sorted(contact_ids), max_contacts=max_contacts)

    rows = []
    for lead in leads:
        lead_id = _to_int(lead.get("id"))
        if lead_id <= 0:
            continue
        contact_ids_for_lead = [
            _to_int(contact.get("id"))
            for contact in ((lead.get("_embedded") or {}).get("contacts") or [])
            if contact.get("id")
        ]
        contact_ids_for_lead = [cid for cid in contact_ids_for_lead if cid > 0]
        phones = []
        for cid in contact_ids_for_lead:
            phones.extend(contacts_map.get(cid, {}).get("phones", []))
        lead_phones = _extract_phones_from_custom_fields(lead.get("custom_fields_values") or [])
        phones.extend(lead_phones)
        normalized_phones = [p for p in {_normalize_phone(p) for p in phones} if p]

        rows.append(
            {
                "deal_id": lead_id,
                "deal_name": (lead.get("name") or f"Deal {lead_id}").strip() or f"Deal {lead_id}",
                "status_id": lead.get("status_id"),
                "status": status_map.get(_to_int(lead.get("status_id"))) or "",
                "responsible": str(lead.get("responsible_user_id") or ""),
                "phone": normalized_phones[0] if normalized_phones else "",
                "chat_id": None,
                "first_message_at": None,
                "last_message_at": None,
                "messages_count": 0,
                "deal_attrs_json": {
                    "source": "amocrm",
                    "updated_at": lead.get("updated_at"),
                    "pipeline_id": lead.get("pipeline_id"),
                    "loss_reason_id": lead.get("loss_reason_id"),
                },
                "contact_attrs_json": {
                    "contact_ids": contact_ids_for_lead,
                    "phones": normalized_phones,
                },
                "dialog_raw": [],
                "dialog_norm": "",
                "comment": "",
                "_phones": normalized_phones,
            }
        )
    return rows


def _amo_status_map(base_url: str, token: str) -> dict[int, str]:
    payload = _request_json(
        "GET",
        f"{base_url}/api/v4/leads/pipelines",
        headers={"Authorization": f"Bearer {token}", "User-Agent": "synkro/1.0"},
    )
    result: dict[int, str] = {}
    for pipeline in (payload.get("_embedded") or {}).get("pipelines", []) or []:
        for status in (pipeline.get("_embedded") or {}).get("statuses", []) or []:
            sid = _to_int(status.get("id"))
            if sid > 0:
                result[sid] = (status.get("name") or "").strip()
    return result


def _amo_fetch_leads(
    base_url: str, token: str, window_start: datetime, window_end: datetime, *, max_leads: int
) -> list[dict]:
    from_ts = int(window_start.astimezone(dt_timezone.utc).timestamp())
    to_ts = int(window_end.astimezone(dt_timezone.utc).timestamp())
    params = {
        "with": "contacts",
        "limit": "250",
        "page": "1",
        "filter[updated_at][from]": str(from_ts),
        "filter[updated_at][to]": str(to_ts),
    }
    next_url = f"{base_url}/api/v4/leads?{urlencode(params)}"
    all_leads: list[dict] = []
    page_no = 0
    while next_url and len(all_leads) < max_leads and page_no < 50:
        payload = _request_json(
            "GET",
            next_url,
            headers={"Authorization": f"Bearer {token}", "User-Agent": "synkro/1.0"},
            timeout=15,
            max_attempts=3,
        )
        batch = (payload.get("_embedded") or {}).get("leads", []) or []
        remaining = max_leads - len(all_leads)
        all_leads.extend(batch[:remaining])
        next_url = ((payload.get("_links") or {}).get("next") or {}).get("href")
        page_no += 1
    return all_leads


def _amo_fetch_contacts(
    base_url: str, token: str, contact_ids: list[int], *, max_contacts: int
) -> dict[int, dict]:
    result: dict[int, dict] = {}
    for contact_id in contact_ids[:max_contacts]:
        payload = _request_json(
            "GET",
            f"{base_url}/api/v4/contacts/{contact_id}",
            headers={"Authorization": f"Bearer {token}", "User-Agent": "synkro/1.0"},
            timeout=15,
            max_attempts=3,
        )
        phones = _extract_phones_from_custom_fields(payload.get("custom_fields_values") or [])
        result[contact_id] = {"phones": [p for p in {_normalize_phone(x) for x in phones} if p]}
    return result


def _extract_phones_from_custom_fields(custom_fields: list[dict]) -> list[str]:
    phones: list[str] = []
    for field in custom_fields:
        code = (field.get("field_code") or "").strip().upper()
        name = (field.get("field_name") or "").lower()
        if code != "PHONE" and "tel" not in name and "phone" not in name:
            continue
        for value_item in field.get("values") or []:
            raw = str(value_item.get("value") or "").strip()
            if raw:
                phones.append(raw)
    return phones


def _collect_radist_dialogs(
    radist_public: dict,
    radist_secret: dict,
    *,
    window_start: datetime,
    window_end: datetime,
    fetch_limit: int,
    target_phones: set[str] | None,
    max_contact_pages: int,
    max_candidates: int,
    max_message_pages: int,
) -> list[dict]:
    api_key = (radist_secret.get("api_key") or "").strip()
    company_id = _to_int(radist_public.get("company_id"))
    base_url = (radist_public.get("api_base_url") or "https://api.radist.online/v2").rstrip("/")
    if not api_key or not company_id:
        raise ConnectorError("Radist credentials are incomplete.")

    headers = {"X-Api-Key": api_key, "User-Agent": "synkro/1.0"}
    sources = _request_json(
        "GET",
        f"{base_url}/companies/{company_id}/messaging/chats/sources/",
        headers=headers,
        timeout=15,
        max_attempts=3,
    )
    connection_ids = {
        _to_int(item.get("connection_id"))
        for item in (sources or [])
        if (item.get("type") or "").strip().lower() in {"whatsapp", "waba"}
        and item.get("connection_id")
    }
    connection_ids.discard(0)
    if not connection_ids:
        return []

    contacts = []
    cursor = None
    page_no = 0
    while True:
        params = {"limit": "100"}
        if cursor:
            params["cursor"] = cursor
        payload = _request_json(
            "GET",
            f"{base_url}/companies/{company_id}/messaging/chats/with_contacts/?{urlencode(params)}",
            headers=headers,
            timeout=15,
            max_attempts=3,
        )
        contacts.extend(payload.get("data") or [])
        cursor = ((payload.get("response_metadata") or {}).get("next_cursor") or "").strip()
        page_no += 1
        if not cursor:
            break
        if page_no >= max_contact_pages:
            break

    candidates = []
    target_phones = target_phones or set()
    for contact in contacts:
        contact_name = (contact.get("contact_name") or "").strip()
        contact_id = contact.get("contact_id")
        last_chat_updated_at = _parse_datetime(contact.get("last_chat_updated_at"))
        for chat in contact.get("chats") or []:
            connection_id = _to_int(chat.get("connection_id"))
            if connection_id not in connection_ids:
                continue
            phone = _normalize_phone(chat.get("phone") or chat.get("source_chat_id") or "")
            if target_phones and phone not in target_phones:
                continue
            if not phone:
                continue
            candidates.append(
                {
                    "contact_id": contact_id,
                    "contact_name": contact_name,
                    "last_chat_updated_at": last_chat_updated_at,
                    "chat": chat,
                    "phone": phone,
                }
            )
    candidates.sort(
        key=lambda item: item.get("last_chat_updated_at")
        or datetime.min.replace(tzinfo=dt_timezone.utc),
        reverse=True,
    )
    candidate_cap = max_candidates
    if target_phones:
        candidate_cap = min(candidate_cap, max(fetch_limit, len(target_phones) * 2))
    candidates = candidates[:candidate_cap]

    dialogs = []
    for candidate in candidates:
        chat = candidate["chat"]
        chat_id = _to_int(chat.get("chat_id"))
        if chat_id <= 0:
            continue
        messages = _radist_fetch_messages_in_window(
            base_url=base_url,
            company_id=company_id,
            headers=headers,
            chat_id=chat_id,
            window_start=window_start,
            window_end=window_end,
            max_pages=max_message_pages,
        )
        if not messages:
            continue
        first_dt = _parse_datetime(messages[0].get("created_at"))
        last_dt = _parse_datetime(messages[-1].get("created_at"))
        dialogs.append(
            {
                "contact_id": candidate["contact_id"],
                "contact_name": candidate["contact_name"] or candidate["phone"],
                "phone": candidate["phone"],
                "connection_id": chat.get("connection_id"),
                "chat_id": chat_id,
                "source_chat_id": chat.get("source_chat_id"),
                "messages": messages,
                "first_message_at": _dt_to_iso(first_dt) if first_dt else None,
                "last_message_at": _dt_to_iso(last_dt) if last_dt else None,
            }
        )
    return dialogs


def _radist_fetch_messages_in_window(
    *,
    base_url: str,
    company_id: int,
    headers: dict,
    chat_id: int,
    window_start: datetime,
    window_end: datetime,
    max_pages: int,
) -> list[dict]:
    all_messages = []
    seen = set()
    until = None
    for _ in range(max_pages):
        params = {"chat_id": str(chat_id), "limit": "100"}
        if until:
            params["until"] = until
        endpoint = f"{base_url}/companies/{company_id}/messaging/messages/?{urlencode(params)}"
        batch = _request_json(
            "GET",
            endpoint,
            headers=headers,
            timeout=15,
            max_attempts=3,
        )
        if not isinstance(batch, list) or not batch:
            break

        oldest = None
        for message in batch:
            message_id = message.get("message_id")
            if message_id and message_id in seen:
                continue
            if message_id:
                seen.add(message_id)
            created_at = _parse_datetime(message.get("created_at"))
            if not created_at:
                continue
            if window_start <= created_at < window_end:
                all_messages.append(message)
            if oldest is None or created_at < oldest:
                oldest = created_at

        if len(batch) < 100:
            break
        if oldest is None or oldest < window_start:
            break
        until = _dt_to_iso(oldest - timedelta(milliseconds=1))
    all_messages.sort(key=lambda item: _parse_datetime(item.get("created_at")) or datetime.min.replace(tzinfo=dt_timezone.utc))
    return all_messages


def _merge_rows(
    *, tenant_slug: str, mode: str, amo_rows: list[dict], radist_dialogs: list[dict]
) -> list[dict]:
    if mode == "amocrm_only":
        return [_finalize_supabase_row(tenant_slug, row) for row in amo_rows]
    if mode == "radist_only":
        rows = []
        for dialog in radist_dialogs:
            synthetic_id = -abs(_stable_numeric_id(f"radist:{dialog.get('chat_id') or dialog.get('source_chat_id') or dialog.get('phone')}"))
            rows.append(
                _finalize_supabase_row(
                    tenant_slug,
                    {
                        "deal_id": synthetic_id,
                        "deal_name": (dialog.get("contact_name") or dialog.get("phone") or f"Chat {dialog.get('chat_id')}"),
                        "status_id": None,
                        "status": "radist_chat",
                        "responsible": "",
                        "phone": dialog.get("phone") or "",
                        "chat_id": dialog.get("chat_id"),
                        "first_message_at": dialog.get("first_message_at"),
                        "last_message_at": dialog.get("last_message_at"),
                        "messages_count": len(dialog.get("messages") or []),
                        "deal_attrs_json": {
                            "source": "radist",
                            "connection_id": dialog.get("connection_id"),
                            "mode": mode,
                        },
                        "contact_attrs_json": {
                            "contact_id": dialog.get("contact_id"),
                            "contact_name": dialog.get("contact_name"),
                            "source_chat_id": dialog.get("source_chat_id"),
                        },
                        "dialog_raw": dialog.get("messages") or [],
                        "dialog_norm": _format_dialog_norm(dialog.get("messages") or []),
                        "comment": "",
                    },
                )
            )
        return rows

    # amocrm_radist
    dialog_by_phone = {}
    for dialog in radist_dialogs:
        phone = dialog.get("phone") or ""
        if not phone:
            continue
        prev = dialog_by_phone.get(phone)
        if not prev:
            dialog_by_phone[phone] = dialog
            continue
        if (prev.get("last_message_at") or "") < (dialog.get("last_message_at") or ""):
            dialog_by_phone[phone] = dialog

    merged = []
    for row in amo_rows:
        phones = row.get("_phones", [])
        dialog = next((dialog_by_phone.get(phone) for phone in phones if dialog_by_phone.get(phone)), None)
        merged_row = dict(row)
        if dialog:
            merged_row["phone"] = dialog.get("phone") or merged_row.get("phone") or ""
            merged_row["chat_id"] = dialog.get("chat_id")
            merged_row["first_message_at"] = dialog.get("first_message_at")
            merged_row["last_message_at"] = dialog.get("last_message_at")
            merged_row["messages_count"] = len(dialog.get("messages") or [])
            merged_row["dialog_raw"] = dialog.get("messages") or []
            merged_row["dialog_norm"] = _format_dialog_norm(dialog.get("messages") or [])
        merged.append(_finalize_supabase_row(tenant_slug, merged_row))
    return merged


def _finalize_supabase_row(tenant_slug: str, row: dict) -> dict:
    deal_id = int(row.get("deal_id") or 0)
    if deal_id == 0:
        deal_id = _stable_numeric_id(f"deal:{row.get('deal_name')}")
    return {
        "tenant_id": tenant_slug,
        "deal_id": deal_id,
        "deal_name": (row.get("deal_name") or f"Deal {deal_id}")[:500],
        "status_id": row.get("status_id"),
        "status": row.get("status") or "",
        "responsible": row.get("responsible") or "",
        "phone": _normalize_phone(row.get("phone") or ""),
        "chat_id": row.get("chat_id"),
        "first_message_at": row.get("first_message_at"),
        "last_message_at": row.get("last_message_at"),
        "messages_count": int(row.get("messages_count") or 0),
        "deal_attrs_json": row.get("deal_attrs_json") or {},
        "contact_attrs_json": row.get("contact_attrs_json") or {},
        "dialog_raw": row.get("dialog_raw") or [],
        "dialog_norm": row.get("dialog_norm") or "",
        "comment": row.get("comment") or "",
    }


def _supabase_upsert_deals(supabase_url: str, service_key: str, rows: list[dict]) -> None:
    endpoint = f"{supabase_url}/rest/v1/deals?on_conflict=tenant_id,deal_id"
    _request_json(
        "POST",
        endpoint,
        headers={
            "apikey": service_key,
            "Authorization": f"Bearer {service_key}",
            "Content-Type": "application/json; charset=utf-8",
            "Prefer": "resolution=merge-duplicates,return=minimal",
            "User-Agent": "synkro-etl/1.0",
        },
        payload=rows,
    )


def _request_json(
    method: str,
    url: str,
    *,
    headers: dict | None = None,
    payload=None,
    timeout: int = 30,
    max_attempts: int = 6,
):
    last_error = None
    for attempt in range(1, max_attempts + 1):
        req = Request(url, method=method)
        if headers:
            for key, value in headers.items():
                req.add_header(key, value)
        body = None
        if payload is not None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        try:
            with urlopen(req, data=body, timeout=timeout) as response:
                raw = response.read().decode("utf-8") if response else ""
                if not raw:
                    return {}
                return json.loads(raw)
        except HTTPError as exc:
            error_body = ""
            try:
                error_body = exc.read().decode("utf-8")
            except Exception:
                error_body = ""
            retriable = exc.code in {408, 425, 429, 500, 502, 503, 504}
            last_error = f"HTTP {exc.code}: {error_body[:500]}"
            if not retriable or attempt >= max_attempts:
                raise ConnectorError(last_error) from exc
        except URLError as exc:
            last_error = f"Network error: {exc.reason}"
            if attempt >= max_attempts:
                raise ConnectorError(last_error) from exc
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            last_error = "Failed to parse API response."
            raise ConnectorError(last_error) from exc
        time.sleep(min(2 * attempt, 15))
    raise ConnectorError(last_error or "Request failed")


def _normalize_phone(value: str) -> str:
    raw = str(value or "")
    digits = "".join(ch for ch in raw if ch.isdigit())
    if not digits:
        return ""
    if len(digits) == 10:
        digits = "7" + digits
    elif len(digits) == 11 and digits.startswith("8"):
        digits = "7" + digits[1:]
    elif len(digits) > 11:
        digits = digits[-11:]
    return digits


def _parse_datetime(value) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        text = str(value).strip()
        if text.isdigit():
            return datetime.fromtimestamp(int(text), tz=dt_timezone.utc)
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            dt = datetime.fromisoformat(text)
        except ValueError:
            return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=dt_timezone.utc)
    return dt.astimezone(dt_timezone.utc)


def _dt_to_iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.astimezone(dt_timezone.utc).isoformat().replace("+00:00", "Z")


def _stable_numeric_id(seed: str) -> int:
    digest = hashlib.sha256(seed.encode("utf-8")).hexdigest()[:15]
    return int(digest, 16)


def _bounded_int(value, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(maximum, parsed))


def _to_int(value) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _format_dialog_norm(messages: list[dict]) -> str:
    lines = []
    sorted_messages = sorted(
        messages,
        key=lambda item: _parse_datetime(item.get("created_at")) or datetime.min.replace(tzinfo=dt_timezone.utc),
    )
    for message in sorted_messages:
        created = _parse_datetime(message.get("created_at"))
        ts = created.astimezone(dt_timezone.utc).strftime("%Y-%m-%d %H:%M:%S") if created else "unknown-time"
        direction = str(message.get("direction") or "").lower()
        actor = "client" if direction == "inbound" else "agent"
        text = _extract_message_text(message)
        files = _extract_attachments(message)
        line = f"{ts}  {actor}:"
        if text:
            line += f" {text}"
        if files:
            line += " [files: " + ", ".join(sorted(set(files))) + "]"
        lines.append(line)
    return "\n".join(lines)


def _extract_message_text(message: dict) -> str:
    text_value = ((message.get("text") or {}).get("text") or "").strip()
    if text_value:
        return text_value
    interactive = (message.get("waba_interactive") or {}).get("body") or {}
    interactive_text = (interactive.get("text") or "").strip()
    if interactive_text:
        return interactive_text
    for key in ("file", "image", "audio", "video", "voice"):
        caption = ((message.get(key) or {}).get("caption") or "").strip()
        if caption:
            return caption
    return ""


def _extract_attachments(message: dict) -> list[str]:
    names = []
    for key in ("file", "image", "audio", "video", "voice"):
        info = message.get(key) or {}
        name = (info.get("name") or "").strip()
        if name:
            names.append(name)
    return names
