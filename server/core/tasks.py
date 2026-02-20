import logging

from celery import shared_task
from django.utils import timezone

from .models import JobRun, Tenant
from .pipeline import (
    build_job_idempotency_key,
    compute_last_closed_window,
    execute_pipeline_job,
    get_or_create_runtime_config,
    is_schedule_due,
    queue_report_job,
)

logger = logging.getLogger(__name__)


@shared_task(name="core.scheduler_tick")
def scheduler_tick() -> int:
    now_utc = timezone.now()
    queued = 0
    active_tenants = Tenant.objects.filter(status=Tenant.Status.ACTIVE).order_by("id")
    for tenant in active_tenants:
        config = get_or_create_runtime_config(tenant)
        if not config.is_schedule_enabled:
            continue
        if not is_schedule_due(config, now_utc):
            continue
        window_start, window_end = compute_last_closed_window(config, now_utc)
        idempotency_key = build_job_idempotency_key(
            config.tenant_id,
            JobRun.TriggerType.SCHEDULED,
            config.mode,
            window_start,
            window_end,
        )
        try:
            _, created = queue_report_job(
                tenant=config.tenant,
                runtime_config=config,
                trigger_type=JobRun.TriggerType.SCHEDULED,
                window_start=window_start,
                window_end=window_end,
                requested_by=None,
                idempotency_key=idempotency_key,
                metadata={"source": "celery_beat"},
            )
            if created:
                queued += 1
        except Exception:
            logger.exception("Failed to queue scheduled job for tenant %s", config.tenant.slug)
    return queued


@shared_task(name="core.run_pipeline_job")
def run_pipeline_job(job_id: int) -> None:
    execute_pipeline_job(job_id)
