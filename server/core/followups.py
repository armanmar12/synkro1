import json
import logging
import re
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from django.utils import timezone

from .crypto import decrypt_payload
from .models import IntegrationConfig, Report, ReportMessage, Tenant
from .pipeline import PipelineError, _call_ai

logger = logging.getLogger(__name__)

_MENTION_RE = re.compile(r"@[A-Za-z0-9_]{2,64}")


def build_report_followup_answer(
    *,
    report: Report,
    question: str,
    history: list[tuple[str, str]] | None = None,
) -> str:
    integrations = {
        cfg.kind: cfg for cfg in IntegrationConfig.objects.filter(tenant=report.tenant)
    }
    ai_config = integrations.get(IntegrationConfig.Kind.AI)
    if not ai_config:
        raise PipelineError("AI integration is not configured for this tenant.")

    ai_public = ai_config.public_config or {}
    ai_secret = decrypt_payload(ai_config.secret_data_encrypted)
    provider = (ai_public.get("provider") or "").strip().lower()
    model = (ai_public.get("model") or "").strip()
    api_key = (ai_secret.get("api_key") or "").strip()
    if not provider:
        raise PipelineError("AI provider is not configured.")
    if not api_key:
        raise PipelineError("AI API key is not configured.")

    followup_prompt = (
        ai_public.get("followup_prompt")
        or "Answer follow-up questions about the report clearly and only from available context."
    )
    context_lines = [
        f"Tenant: {report.tenant.slug}",
        f"Report ID: {report.id}",
        f"Report type: {report.report_type}",
        f"Window start: {report.window_start.isoformat() if report.window_start else '-'}",
        f"Window end: {report.window_end.isoformat() if report.window_end else '-'}",
        "Report text:",
        report.summary_text or "-",
    ]

    summary = (report.metadata or {}).get("summary")
    if isinstance(summary, dict) and summary:
        context_lines.append(f"Summary JSON: {json.dumps(summary, ensure_ascii=False)}")

    if history:
        context_lines.append("Previous follow-up Q&A:")
        for previous_question, previous_answer in history[-6:]:
            if previous_question:
                context_lines.append(f"Q: {previous_question.strip()}")
            if previous_answer:
                context_lines.append(f"A: {previous_answer.strip()}")

    context_lines.append(f"User question: {question.strip()}")
    context = "\n".join(context_lines)
    return _call_ai(
        provider=provider,
        model=model,
        api_key=api_key,
        prompt=followup_prompt,
        context=context,
    )


def process_telegram_followups() -> int:
    processed = 0
    tenants = Tenant.objects.filter(status=Tenant.Status.ACTIVE).order_by("id")
    for tenant in tenants:
        try:
            processed += _process_tenant_telegram_followups(tenant)
        except Exception:
            logger.exception("Telegram follow-up failed for tenant %s", tenant.slug)
    return processed


def _process_tenant_telegram_followups(tenant: Tenant) -> int:
    telegram_config = (
        IntegrationConfig.objects.filter(tenant=tenant, kind=IntegrationConfig.Kind.TELEGRAM).first()
    )
    if not telegram_config:
        return 0

    ai_config = IntegrationConfig.objects.filter(tenant=tenant, kind=IntegrationConfig.Kind.AI).first()
    if not ai_config:
        return 0

    public_config = dict(telegram_config.public_config or {})
    secret_config = decrypt_payload(telegram_config.secret_data_encrypted)
    bot_token = (secret_config.get("bot_token") or "").strip()
    chat_id = str(public_config.get("chat_id") or "").strip()
    if not bot_token or not chat_id:
        return 0

    saved_offset = public_config.get("telegram_update_offset")
    offset = _safe_int(saved_offset)
    updates = _telegram_get_updates(bot_token, offset)
    if not updates:
        return 0

    max_update_id = max(int(update.get("update_id") or 0) for update in updates)
    if offset is None and len(updates) > 20:
        updates = updates[-20:]

    processed = 0
    now_ts = int(timezone.now().timestamp())
    for update in updates:
        update_id = int(update.get("update_id") or 0)
        message = update.get("message") or update.get("edited_message") or {}
        if not isinstance(message, dict):
            continue
        if bool((message.get("from") or {}).get("is_bot")):
            continue

        message_chat_id = str(((message.get("chat") or {}).get("id")) or "").strip()
        if message_chat_id != chat_id:
            continue
        if offset is None:
            message_ts = _safe_int(message.get("date"))
            if message_ts is not None and message_ts < (now_ts - 3600):
                continue

        tagged_question = _extract_tagged_question(
            text=(message.get("text") or ""),
            entities=message.get("entities") or [],
        )
        if tagged_question is None:
            continue
        if not tagged_question:
            _send_telegram_message(
                bot_token,
                chat_id,
                "Напишите вопрос после @упоминания, например: @synkro что пошло не так по лидам?",
            )
            continue

        report = Report.objects.filter(tenant=tenant).order_by("-created_at").first()
        if not report:
            _send_telegram_message(bot_token, chat_id, "Для этого tenant пока нет отчета.")
            continue

        history = list(
            report.messages.order_by("-created_at")
            .values_list("question", "answer")[:6]
        )
        history.reverse()
        try:
            answer = build_report_followup_answer(
                report=report,
                question=tagged_question,
                history=history,
            )
        except PipelineError as exc:
            answer = f"AI follow-up error: {exc}"

        ReportMessage.objects.create(
            report=report,
            actor=None,
            question=f"[telegram update {update_id}] {tagged_question}",
            answer=answer,
        )
        _send_telegram_message(bot_token, chat_id, answer)
        processed += 1

    _save_update_offset(telegram_config, max_update_id + 1)
    return processed


def _extract_tagged_question(text: str, entities: list[dict]) -> str | None:
    if not isinstance(text, str):
        return None
    text = text.strip()
    if not text:
        return None

    has_mention = False
    if isinstance(entities, list):
        for entity in entities:
            if not isinstance(entity, dict):
                continue
            if (entity.get("type") or "").strip() == "mention":
                has_mention = True
                break
    if not has_mention and "@" in text and _MENTION_RE.search(text):
        has_mention = True
    if not has_mention:
        return None

    cleaned = _MENTION_RE.sub(" ", text)
    cleaned = " ".join(cleaned.split()).strip(" ,;:-")
    return cleaned


def _telegram_get_updates(bot_token: str, offset: int | None) -> list[dict]:
    params = {"timeout": "0", "limit": "50"}
    if offset is not None:
        params["offset"] = str(offset)
    endpoint = f"https://api.telegram.org/bot{bot_token}/getUpdates?{urlencode(params)}"
    req = Request(endpoint, headers={"User-Agent": "synkro/1.0"}, method="GET")
    try:
        with urlopen(req, timeout=12) as response:
            payload = json.loads(response.read().decode("utf-8") or "{}")
    except Exception:
        logger.exception("Failed to fetch Telegram updates")
        return []

    if not isinstance(payload, dict) or payload.get("ok") is not True:
        return []
    result = payload.get("result") or []
    if not isinstance(result, list):
        return []
    return result


def _send_telegram_message(bot_token: str, chat_id: str, text: str) -> None:
    message = (text or "").strip() or "Empty response."
    if len(message) > 3900:
        message = message[:3900] + "..."
    req = Request(
        f"https://api.telegram.org/bot{bot_token}/sendMessage",
        headers={"Content-Type": "application/json"},
        data=json.dumps({"chat_id": chat_id, "text": message}).encode("utf-8"),
        method="POST",
    )
    try:
        with urlopen(req, timeout=12):
            return
    except (HTTPError, URLError):
        logger.exception("Failed to send Telegram follow-up message")


def _save_update_offset(telegram_config: IntegrationConfig, offset: int) -> None:
    public_config = dict(telegram_config.public_config or {})
    public_config["telegram_update_offset"] = int(offset)
    telegram_config.public_config = public_config
    telegram_config.save(update_fields=["public_config", "updated_at"])


def _safe_int(value) -> int | None:
    try:
        if value in (None, ""):
            return None
        return int(value)
    except (TypeError, ValueError):
        return None
