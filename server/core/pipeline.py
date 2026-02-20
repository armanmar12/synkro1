import json
import logging
from datetime import datetime, timedelta, timezone as dt_timezone
from zoneinfo import ZoneInfo
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from .connectors import ConnectorError, sync_sources_to_supabase
from .crypto import decrypt_payload
from .models import AuditLog, IntegrationConfig, JobRun, JobRunEvent, Report, Tenant, TenantRuntimeConfig

logger = logging.getLogger(__name__)


class PipelineError(Exception):
    pass


def get_or_create_runtime_config(tenant: Tenant) -> TenantRuntimeConfig:
    config, created = TenantRuntimeConfig.objects.get_or_create(
        tenant=tenant,
        defaults={"timezone": tenant.timezone},
    )
    if not created and not config.timezone:
        config.timezone = tenant.timezone
        config.save(update_fields=["timezone", "updated_at"])
    return config


def get_timezone(config: TenantRuntimeConfig) -> ZoneInfo:
    tz_name = (config.timezone or "").strip() or config.tenant.timezone or settings.TIME_ZONE
    try:
        return ZoneInfo(tz_name)
    except Exception:
        return ZoneInfo(settings.TIME_ZONE)


def compute_last_closed_window(
    config: TenantRuntimeConfig, now_utc: datetime | None = None
) -> tuple[datetime, datetime]:
    now_utc = now_utc or timezone.now()
    tz = get_timezone(config)
    now_local = now_utc.astimezone(tz)
    current_day_start = datetime.combine(now_local.date(), config.business_day_start, tzinfo=tz)
    if now_local < current_day_start:
        current_day_start -= timedelta(days=1)
    window_end_local = current_day_start
    window_start_local = window_end_local - timedelta(days=1)
    return window_start_local.astimezone(dt_timezone.utc), window_end_local.astimezone(dt_timezone.utc)


def is_schedule_due(config: TenantRuntimeConfig, now_utc: datetime | None = None) -> bool:
    if not config.is_schedule_enabled:
        return False
    now_utc = now_utc or timezone.now()
    tz = get_timezone(config)
    now_local = now_utc.astimezone(tz)
    scheduled_local = datetime.combine(now_local.date(), config.scheduled_run_time, tzinfo=tz)
    seconds_since_schedule = (now_local - scheduled_local).total_seconds()
    return 0 <= seconds_since_schedule < 180


def build_job_idempotency_key(
    tenant_id: int, trigger_type: str, mode: str, window_start: datetime, window_end: datetime
) -> str:
    start_key = window_start.astimezone(dt_timezone.utc).strftime("%Y%m%dT%H%M")
    end_key = window_end.astimezone(dt_timezone.utc).strftime("%Y%m%dT%H%M")
    return f"{trigger_type}:{tenant_id}:{mode}:{start_key}:{end_key}"


def validate_forced_window(
    config: TenantRuntimeConfig, window_start: datetime, window_end: datetime
) -> str | None:
    if window_end <= window_start:
        return "Период задан неверно: дата окончания должна быть больше даты начала."
    window_hours = (window_end - window_start).total_seconds() / 3600
    if window_hours > config.max_force_window_hours:
        return f"Слишком длинный период. Максимум: {config.max_force_window_hours} ч."
    lookback_limit = timezone.now() - timedelta(days=config.max_force_lookback_days)
    if window_start < lookback_limit:
        return f"Период слишком старый. Допустимо не более {config.max_force_lookback_days} дн назад."
    return None


def queue_report_job(
    *,
    tenant: Tenant,
    runtime_config: TenantRuntimeConfig,
    trigger_type: str,
    window_start: datetime,
    window_end: datetime,
    requested_by=None,
    idempotency_key: str | None = None,
    metadata: dict | None = None,
) -> tuple[JobRun, bool]:
    idempotency_key = idempotency_key or build_job_idempotency_key(
        tenant.id, trigger_type, runtime_config.mode, window_start, window_end
    )

    existing = JobRun.objects.filter(idempotency_key=idempotency_key).first()
    if existing:
        return existing, False

    with transaction.atomic():
        existing_locked = (
            JobRun.objects.select_for_update().filter(idempotency_key=idempotency_key).first()
        )
        if existing_locked:
            return existing_locked, False

        job = JobRun.objects.create(
            tenant=tenant,
            job_type=JobRun.JobType.REPORT_BUILD,
            mode=runtime_config.mode,
            trigger_type=trigger_type,
            status=JobRun.Status.PENDING,
            current_step="Queued",
            progress=0,
            window_start=window_start,
            window_end=window_end,
            requested_by=requested_by if getattr(requested_by, "is_authenticated", False) else None,
            idempotency_key=idempotency_key,
            metadata=metadata or {},
        )

    _write_job_event(job, JobRunEvent.Level.INFO, "Queued", {"trigger_type": trigger_type})

    _write_audit(
        tenant=tenant,
        actor=requested_by if getattr(requested_by, "is_authenticated", False) else None,
        action="report_job_queued",
        message=f"Report job queued ({trigger_type})",
        metadata={
            "job_id": job.id,
            "mode": runtime_config.mode,
            "window_start": window_start.isoformat(),
            "window_end": window_end.isoformat(),
        },
    )

    from .tasks import run_pipeline_job

    try:
        run_pipeline_job.delay(job.id)
    except Exception as exc:
        job.status = JobRun.Status.FAILED
        job.error = f"Failed to enqueue Celery task: {exc}"
        job.finished_at = timezone.now()
        job.save(update_fields=["status", "error", "finished_at", "updated_at"])
        raise PipelineError(job.error) from exc

    return job, True


def execute_pipeline_job(job_id: int) -> None:
    job = JobRun.objects.select_related("tenant").filter(id=job_id).first()
    if not job:
        raise PipelineError(f"JobRun {job_id} not found")
    if job.status == JobRun.Status.SUCCESS:
        return

    config = get_or_create_runtime_config(job.tenant)
    if not job.window_start or not job.window_end:
        window_start, window_end = compute_last_closed_window(config)
        job.window_start = window_start
        job.window_end = window_end
        job.save(update_fields=["window_start", "window_end", "updated_at"])
    integrations = _load_integrations(job.tenant)
    try:
        _mark_running(job, "Checking tenant configuration", 5)
        _validate_integrations(config, integrations)

        _mark_running(job, "Syncing source systems", 25)
        sync_stats = _sync_sources(job, config, integrations)
        _attach_job_metadata(job, {"sync_stats": sync_stats})
        _write_job_event(job, JobRunEvent.Level.INFO, "Sources synced", sync_stats)

        _mark_running(job, "Loading data from Supabase", 45)
        records = _fetch_deals_for_window(job.tenant, config, integrations, job.window_start, job.window_end)
        _write_job_event(job, JobRunEvent.Level.INFO, "Deals loaded", {"count": len(records)})

        _mark_running(job, "Preparing report", 65)
        summary = _build_summary(records)
        summary["sync"] = sync_stats
        _write_job_event(job, JobRunEvent.Level.INFO, "Summary prepared", {"summary": summary})
        report_text, ai_meta = _generate_report_text(
            tenant=job.tenant,
            config=config,
            integrations=integrations,
            window_start=job.window_start,
            window_end=job.window_end,
            records=records,
            summary=summary,
        )

        _mark_running(job, "Saving report", 82)
        report = _save_report(job, config, report_text, summary, ai_meta)
        _write_job_event(job, JobRunEvent.Level.INFO, "Report saved (DB)", {"report_id": report.id})
        _push_report_to_supabase(job.tenant, integrations, report)

        _mark_running(job, "Sending Telegram notification", 95)
        delivered = _send_telegram_notification(job.tenant, integrations, report)
        _write_job_event(job, JobRunEvent.Level.INFO, "Telegram send attempted", {"delivered": delivered})
        if delivered:
            report.status = Report.Status.SENT
            report.save(update_fields=["status", "updated_at"])

        job.status = JobRun.Status.SUCCESS
        job.current_step = "Done"
        job.progress = 100
        job.error = ""
        if not job.started_at:
            job.started_at = timezone.now()
        job.finished_at = timezone.now()
        job.save(
            update_fields=[
                "status",
                "current_step",
                "progress",
                "error",
                "started_at",
                "finished_at",
                "updated_at",
            ]
        )
        _write_job_event(job, JobRunEvent.Level.INFO, "Done", {"report_id": report.id})
        _write_audit(
            tenant=job.tenant,
            actor=job.requested_by,
            action="report_job_success",
            message="Report job finished successfully",
            metadata={"job_id": job.id, "report_id": report.id},
        )
    except PipelineError as exc:
        _write_job_event(job, JobRunEvent.Level.ERROR, "PipelineError", {"error": str(exc)})
        _mark_failed(job, str(exc))
    except ConnectorError as exc:
        _write_job_event(job, JobRunEvent.Level.ERROR, "ConnectorError", {"error": str(exc)})
        _mark_failed(job, str(exc))
    except Exception as exc:
        logger.exception("Unexpected pipeline error for job %s", job.id)
        _write_job_event(job, JobRunEvent.Level.ERROR, "Unexpected error", {"error": str(exc)})
        _mark_failed(job, f"Unexpected error: {exc}")


def _sync_sources(
    job: JobRun, config: TenantRuntimeConfig, integrations: dict[str, IntegrationConfig]
) -> dict:
    supabase = integrations[IntegrationConfig.Kind.SUPABASE]
    supabase_public = supabase.public_config or {}
    supabase_secret = decrypt_payload(supabase.secret_data_encrypted)
    amocrm_public = None
    amocrm_secret = None
    radist_public = None
    radist_secret = None
    if IntegrationConfig.Kind.AMOCRM in integrations:
        amocrm_public = integrations[IntegrationConfig.Kind.AMOCRM].public_config or {}
        amocrm_secret = decrypt_payload(
            integrations[IntegrationConfig.Kind.AMOCRM].secret_data_encrypted
        )
    if IntegrationConfig.Kind.RADIST in integrations:
        radist_public = integrations[IntegrationConfig.Kind.RADIST].public_config or {}
        radist_secret = decrypt_payload(
            integrations[IntegrationConfig.Kind.RADIST].secret_data_encrypted
        )

    return sync_sources_to_supabase(
        tenant_slug=job.tenant.slug,
        mode=config.mode,
        window_start=job.window_start,
        window_end=job.window_end,
        runtime_config=config,
        supabase_public=supabase_public,
        supabase_secret=supabase_secret,
        amocrm_public=amocrm_public,
        amocrm_secret=amocrm_secret,
        radist_public=radist_public,
        radist_secret=radist_secret,
    )


def _mark_running(job: JobRun, step: str, progress: int) -> None:
    if job.status != JobRun.Status.RUNNING:
        job.status = JobRun.Status.RUNNING
    job.current_step = step
    job.progress = max(0, min(99, progress))
    if not job.started_at:
        job.started_at = timezone.now()
    job.save(update_fields=["status", "current_step", "progress", "started_at", "updated_at"])
    _write_job_event(job, JobRunEvent.Level.INFO, step, {"progress": job.progress})


def _mark_failed(job: JobRun, error: str) -> None:
    job.status = JobRun.Status.FAILED
    job.error = error
    job.current_step = "Failed"
    job.finished_at = timezone.now()
    job.save(
        update_fields=["status", "error", "current_step", "finished_at", "updated_at"]
    )
    _write_job_event(job, JobRunEvent.Level.ERROR, "Failed", {"error": error})
    _write_audit(
        tenant=job.tenant,
        actor=job.requested_by,
        action="report_job_failed",
        message=error,
        metadata={"job_id": job.id},
    )


def _attach_job_metadata(job: JobRun, values: dict) -> None:
    payload = job.metadata or {}
    payload.update(values or {})
    job.metadata = payload
    job.save(update_fields=["metadata", "updated_at"])


def _load_integrations(tenant: Tenant) -> dict[str, IntegrationConfig]:
    configs = IntegrationConfig.objects.filter(tenant=tenant)
    return {cfg.kind: cfg for cfg in configs}


def _validate_integrations(
    runtime_config: TenantRuntimeConfig, integrations: dict[str, IntegrationConfig]
) -> None:
    required = [IntegrationConfig.Kind.SUPABASE, IntegrationConfig.Kind.AI]
    if runtime_config.mode in {
        TenantRuntimeConfig.Mode.AMOCRM_RADIST,
        TenantRuntimeConfig.Mode.AMOCRM_ONLY,
    }:
        required.append(IntegrationConfig.Kind.AMOCRM)
    if runtime_config.mode in {
        TenantRuntimeConfig.Mode.AMOCRM_RADIST,
        TenantRuntimeConfig.Mode.RADIST_ONLY,
    }:
        required.append(IntegrationConfig.Kind.RADIST)

    for kind in required:
        if kind not in integrations:
            raise PipelineError(f"Missing integration config: {kind}")

    supabase = integrations[IntegrationConfig.Kind.SUPABASE]
    supabase_secret = decrypt_payload(supabase.secret_data_encrypted)
    supabase_url = (supabase.public_config or {}).get("url", "").strip()
    if not supabase_url:
        raise PipelineError("Supabase URL is not configured.")
    if not (supabase_secret.get("service_role_key") or supabase_secret.get("service_role_jwt")):
        raise PipelineError("Supabase service role key is not configured.")

    ai = integrations[IntegrationConfig.Kind.AI]
    ai_secret = decrypt_payload(ai.secret_data_encrypted)
    provider = (ai.public_config or {}).get("provider", "").strip()
    if not provider:
        raise PipelineError("AI provider is not configured.")
    if not ai_secret.get("api_key"):
        raise PipelineError("AI API key is not configured.")

    if IntegrationConfig.Kind.AMOCRM in required:
        amocrm = integrations[IntegrationConfig.Kind.AMOCRM]
        amocrm_secret = decrypt_payload(amocrm.secret_data_encrypted)
        if not (amocrm.public_config or {}).get("domain", "").strip():
            raise PipelineError("amoCRM domain is not configured.")
        if not amocrm_secret.get("access_token"):
            raise PipelineError("amoCRM access token is not configured.")

    if IntegrationConfig.Kind.RADIST in required:
        radist = integrations[IntegrationConfig.Kind.RADIST]
        radist_secret = decrypt_payload(radist.secret_data_encrypted)
        if not radist_secret.get("api_key"):
            raise PipelineError("Radist API key is not configured.")
        if not (radist.public_config or {}).get("company_id"):
            raise PipelineError("Radist company_id is not configured.")


def _fetch_deals_for_window(
    tenant: Tenant,
    runtime_config: TenantRuntimeConfig,
    integrations: dict[str, IntegrationConfig],
    window_start: datetime | None,
    window_end: datetime | None,
) -> list[dict]:
    if window_start is None or window_end is None:
        raise PipelineError("Report window is not set.")

    supabase = integrations[IntegrationConfig.Kind.SUPABASE]
    supabase_url = (supabase.public_config or {}).get("url", "").rstrip("/")
    supabase_secret = decrypt_payload(supabase.secret_data_encrypted)
    service_key = supabase_secret.get("service_role_key") or supabase_secret.get("service_role_jwt")
    if not supabase_url or not service_key:
        raise PipelineError("Supabase credentials are incomplete.")

    filter_field = "updated_at"
    if runtime_config.mode in {
        TenantRuntimeConfig.Mode.AMOCRM_RADIST,
        TenantRuntimeConfig.Mode.RADIST_ONLY,
    }:
        filter_field = "last_message_at"

    params = [
        (
            "select",
            "deal_id,deal_name,status,responsible,messages_count,first_message_at,last_message_at,updated_at,dialog_norm,comment",
        ),
        ("tenant_id", f"eq.{tenant.slug}"),
        (filter_field, f"gte.{window_start.astimezone(dt_timezone.utc).isoformat()}"),
        (filter_field, f"lt.{window_end.astimezone(dt_timezone.utc).isoformat()}"),
        ("order", f"{filter_field}.desc"),
        ("limit", str(max(50, runtime_config.radist_fetch_limit))),
    ]
    query = urlencode(params, safe=",:.")
    endpoint = f"{supabase_url}/rest/v1/deals?{query}"
    req = Request(
        endpoint,
        headers={
            "apikey": service_key,
            "Authorization": f"Bearer {service_key}",
            "User-Agent": "synkro/1.0",
        },
        method="GET",
    )
    try:
        with urlopen(req, timeout=15) as response:
            if response.status >= 400:
                raise PipelineError(f"Supabase HTTP {response.status}")
            payload = json.loads(response.read().decode("utf-8") or "[]")
            if not isinstance(payload, list):
                raise PipelineError("Supabase returned invalid deals payload.")
            if runtime_config.mode == TenantRuntimeConfig.Mode.AMOCRM_ONLY:
                return payload
            return [
                row for row in payload if int(row.get("messages_count") or 0) >= runtime_config.min_dialogs_for_report
            ]
    except HTTPError as exc:
        raise PipelineError(f"Supabase HTTP {exc.code}") from exc
    except URLError as exc:
        raise PipelineError(f"Supabase network error: {exc.reason}") from exc
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PipelineError("Supabase response parse error.") from exc


def _build_summary(records: list[dict]) -> dict:
    summary = {
        "total_deals": len(records),
        "with_dialogs": 0,
        "total_messages": 0,
        "status_counts": {},
        "responsible_counts": {},
    }
    for row in records:
        messages_count = int(row.get("messages_count") or 0)
        if messages_count > 0:
            summary["with_dialogs"] += 1
            summary["total_messages"] += messages_count
        status = (row.get("status") or "unknown").strip() or "unknown"
        summary["status_counts"][status] = summary["status_counts"].get(status, 0) + 1
        responsible = (row.get("responsible") or "unknown").strip() or "unknown"
        summary["responsible_counts"][responsible] = summary["responsible_counts"].get(responsible, 0) + 1
    return summary


def _generate_report_text(
    *,
    tenant: Tenant,
    config: TenantRuntimeConfig,
    integrations: dict[str, IntegrationConfig],
    window_start: datetime,
    window_end: datetime,
    records: list[dict],
    summary: dict,
) -> tuple[str, dict]:
    ai_config = integrations[IntegrationConfig.Kind.AI]
    ai_public = ai_config.public_config or {}
    ai_secret = decrypt_payload(ai_config.secret_data_encrypted)
    provider = (ai_public.get("provider") or "").strip().lower()
    model = (ai_public.get("model") or "").strip()
    api_key = (ai_secret.get("api_key") or "").strip()
    prompt = (
        ai_public.get("prompt")
        or "Сформируй структурированный отчет по диалогам/сделкам: итоги, риски, сильные стороны, рекомендации."
    )

    context_lines = [
        f"Tenant: {tenant.slug}",
        f"Mode: {config.mode}",
        f"Window UTC: {window_start.isoformat()} .. {window_end.isoformat()}",
        f"Deals: {summary['total_deals']}",
        f"Dialogs: {summary['with_dialogs']}",
        f"Messages: {summary['total_messages']}",
        "Top statuses: "
        + ", ".join(
            f"{name}={count}"
            for name, count in sorted(
                summary["status_counts"].items(), key=lambda item: item[1], reverse=True
            )[:5]
        ),
    ]
    for row in records[:15]:
        text_preview = (row.get("dialog_norm") or "").strip()
        if len(text_preview) > 350:
            text_preview = text_preview[:350] + "..."
        context_lines.append(
            f"Deal #{row.get('deal_id')}: {row.get('deal_name')}; "
            f"status={row.get('status')}; messages={row.get('messages_count')}; "
            f"responsible={row.get('responsible')}; dialog={text_preview or '-'}"
        )
    context = "\n".join(context_lines)

    try:
        text = _call_ai(provider=provider, model=model, api_key=api_key, prompt=prompt, context=context)
        return text, {"ai_provider": provider, "ai_model": model, "ai_fallback": False}
    except PipelineError as exc:
        fallback = _build_fallback_report(config.mode, window_start, window_end, summary)
        return (
            fallback,
            {
                "ai_provider": provider,
                "ai_model": model,
                "ai_fallback": True,
                "ai_error": str(exc),
            },
        )


def _call_ai(*, provider: str, model: str, api_key: str, prompt: str, context: str) -> str:
    if not provider:
        raise PipelineError("AI provider is not configured.")
    if not api_key:
        raise PipelineError("AI key is missing.")

    if provider in {"openai"}:
        body = {
            "model": model or "gpt-4o-mini",
            "messages": [
                {"role": "system", "content": prompt},
                {"role": "user", "content": context},
            ],
            "temperature": 0.2,
        }
        req = Request(
            "https://api.openai.com/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "User-Agent": "synkro/1.0",
            },
            data=json.dumps(body).encode("utf-8"),
            method="POST",
        )
        try:
            with urlopen(req, timeout=30) as response:
                payload = json.loads(response.read().decode("utf-8") or "{}")
        except HTTPError as exc:
            raise PipelineError(f"AI HTTP {exc.code}") from exc
        except URLError as exc:
            raise PipelineError(f"AI network error: {exc.reason}") from exc
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise PipelineError("AI response parse error") from exc
        message = (
            (payload.get("choices") or [{}])[0].get("message", {}).get("content", "").strip()
        )
        if not message:
            raise PipelineError("AI returned empty response.")
        return message

    if provider in {"gemini", "google", "google_gemini"}:
        model_name = model or "models/gemini-1.5-pro"
        if not model_name.startswith("models/"):
            model_name = f"models/{model_name}"
        endpoint = f"https://generativelanguage.googleapis.com/v1beta/{model_name}:generateContent?key={api_key}"
        body = {
            "contents": [
                {
                    "parts": [
                        {
                            "text": f"{prompt}\n\n{context}",
                        }
                    ]
                }
            ]
        }
        req = Request(
            endpoint,
            headers={"Content-Type": "application/json", "User-Agent": "synkro/1.0"},
            data=json.dumps(body).encode("utf-8"),
            method="POST",
        )
        try:
            with urlopen(req, timeout=30) as response:
                payload = json.loads(response.read().decode("utf-8") or "{}")
        except HTTPError as exc:
            raise PipelineError(f"AI HTTP {exc.code}") from exc
        except URLError as exc:
            raise PipelineError(f"AI network error: {exc.reason}") from exc
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise PipelineError("AI response parse error") from exc

        candidates = payload.get("candidates") or []
        if not candidates:
            raise PipelineError("AI returned no candidates.")
        parts = candidates[0].get("content", {}).get("parts", [])
        text = "\n".join((part.get("text") or "").strip() for part in parts if part.get("text"))
        if not text:
            raise PipelineError("AI returned empty response.")
        return text

    raise PipelineError(f"Unsupported AI provider: {provider}")


def _build_fallback_report(mode: str, window_start: datetime, window_end: datetime, summary: dict) -> str:
    status_line = ", ".join(
        f"{name}: {count}"
        for name, count in sorted(summary["status_counts"].items(), key=lambda item: item[1], reverse=True)[:6]
    ) or "n/a"
    responsible_line = ", ".join(
        f"{name}: {count}"
        for name, count in sorted(
            summary["responsible_counts"].items(), key=lambda item: item[1], reverse=True
        )[:6]
    ) or "n/a"
    return "\n".join(
        [
            "Synkro report (fallback mode, AI unavailable).",
            f"Mode: {mode}",
            f"Window UTC: {window_start.isoformat()} .. {window_end.isoformat()}",
            f"Total deals: {summary['total_deals']}",
            f"Deals with dialogs: {summary['with_dialogs']}",
            f"Total messages: {summary['total_messages']}",
            f"Statuses: {status_line}",
            f"Responsible: {responsible_line}",
            "Action: verify AI integration if you need narrative insights.",
        ]
    )


def _save_report(
    job: JobRun,
    runtime_config: TenantRuntimeConfig,
    report_text: str,
    summary: dict,
    ai_meta: dict,
) -> Report:
    tz = get_timezone(runtime_config)
    local_window_start = job.window_start.astimezone(tz) if job.window_start else timezone.now().astimezone(tz)
    local_window_end = job.window_end.astimezone(tz) if job.window_end else timezone.now().astimezone(tz)
    report = Report.objects.create(
        tenant=job.tenant,
        job_run=job,
        period_start=local_window_start.date(),
        period_end=(local_window_end - timedelta(seconds=1)).date(),
        report_type="daily" if job.trigger_type == JobRun.TriggerType.SCHEDULED else "forced",
        status=Report.Status.READY,
        summary_text=report_text,
        metadata={
            "mode": runtime_config.mode,
            "trigger_type": job.trigger_type,
            "summary": summary,
            **ai_meta,
        },
        window_start=job.window_start,
        window_end=job.window_end,
        followup_deadline_at=timezone.now() + timedelta(minutes=runtime_config.telegram_followup_minutes),
    )
    return report


def _push_report_to_supabase(
    tenant: Tenant, integrations: dict[str, IntegrationConfig], report: Report
) -> None:
    supabase = integrations.get(IntegrationConfig.Kind.SUPABASE)
    if not supabase:
        return
    supabase_url = (supabase.public_config or {}).get("url", "").rstrip("/")
    supabase_secret = decrypt_payload(supabase.secret_data_encrypted)
    service_key = supabase_secret.get("service_role_key") or supabase_secret.get("service_role_jwt")
    if not supabase_url or not service_key:
        return
    payload = [
        {
            "tenant_id": tenant.slug,
            "report_date": report.period_end.isoformat(),
            "type": "daily",
            "text": report.summary_text,
            "comment": report.report_type,
        }
    ]
    endpoint = f"{supabase_url}/rest/v1/reports"
    req = Request(
        endpoint,
        headers={
            "apikey": service_key,
            "Authorization": f"Bearer {service_key}",
            "Content-Type": "application/json",
            "Prefer": "return=minimal",
            "User-Agent": "synkro/1.0",
        },
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
    )
    try:
        with urlopen(req, timeout=15):
            return
    except Exception:
        logger.exception("Failed to push report to Supabase for tenant %s", tenant.slug)


def _send_telegram_notification(
    tenant: Tenant, integrations: dict[str, IntegrationConfig], report: Report
) -> bool:
    telegram = integrations.get(IntegrationConfig.Kind.TELEGRAM)
    if not telegram:
        return False
    public_config = telegram.public_config or {}
    secret = decrypt_payload(telegram.secret_data_encrypted)
    chat_id = (public_config.get("chat_id") or "").strip()
    bot_token = (secret.get("bot_token") or "").strip()
    if not chat_id or not bot_token:
        return False

    text = f"[Synkro] Отчет {tenant.name}\n\n{report.summary_text}"
    if len(text) > 3900:
        text = text[:3900] + "..."
    req = Request(
        f"https://api.telegram.org/bot{bot_token}/sendMessage",
        headers={"Content-Type": "application/json"},
        data=json.dumps({"chat_id": chat_id, "text": text}).encode("utf-8"),
        method="POST",
    )
    try:
        with urlopen(req, timeout=12) as response:
            return response.status < 400
    except Exception:
        logger.exception("Telegram notification failed for tenant %s", tenant.slug)
        return False


def _write_audit(
    *,
    tenant: Tenant | None,
    actor,
    action: str,
    message: str,
    metadata: dict | None = None,
) -> None:
    try:
        AuditLog.objects.create(
            tenant=tenant,
            actor=actor,
            action=action,
            message=message,
            metadata=metadata or {},
        )
    except Exception:
        logger.exception("Failed to write audit log: %s", action)


def _write_job_event(job: JobRun, level: str, message: str, data: dict | None = None) -> None:
    try:
        JobRunEvent.objects.create(job_run=job, level=level, message=message, data=data or {})
    except Exception:
        logger.exception("Failed to write job event for job %s", job.id)
