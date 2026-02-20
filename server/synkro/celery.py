import os

from celery import Celery

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "synkro.settings")

app = Celery("synkro")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()

app.conf.beat_schedule = {
    "tenant-scheduler-tick-every-minute": {
        "task": "core.scheduler_tick",
        "schedule": 60.0,
    },
}
