import os

from celery import Celery

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "synkro.settings")

app = Celery("synkro")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()
