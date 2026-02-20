from django.conf import settings
from django.db import models


class Tenant(models.Model):
    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        PAUSED = "paused", "Paused"
        DISABLED = "disabled", "Disabled"

    name = models.CharField(max_length=200)
    slug = models.SlugField(max_length=120, unique=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.ACTIVE)
    timezone = models.CharField(max_length=64, default=getattr(settings, "TIME_ZONE", "UTC"))
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name


class UserRole(models.Model):
    class Role(models.TextChoices):
        SUPER_ADMIN = "super_admin", "Super Admin"
        ADMIN_LITE = "admin_lite", "Admin Lite"
        USER = "user", "User"

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, null=True, blank=True)
    role = models.CharField(max_length=20, choices=Role.choices)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["user", "tenant"], name="uniq_user_tenant_role"),
        ]
        ordering = ["user_id"]

    def __str__(self) -> str:
        tenant_label = self.tenant.slug if self.tenant else "global"
        return f"{self.user} ({tenant_label}): {self.role}"


class UserProfile(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    phone = models.CharField(max_length=32, blank=True)
    timezone = models.CharField(max_length=64, default=getattr(settings, "TIME_ZONE", "UTC"))
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["user_id"]

    def __str__(self) -> str:
        return f"Profile: {self.user}"


class IntegrationConfig(models.Model):
    class Kind(models.TextChoices):
        SUPABASE = "supabase", "Supabase"
        AMOCRM = "amocrm", "amoCRM"
        RADIST = "radist", "Radist"
        AI = "ai", "AI"
        TELEGRAM = "telegram", "Telegram"

    class Status(models.TextChoices):
        UNKNOWN = "unknown", "Unknown"
        OK = "ok", "OK"
        ERROR = "error", "Error"
        PENDING = "pending", "Pending"

    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE)
    kind = models.CharField(max_length=20, choices=Kind.choices)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.UNKNOWN)
    public_config = models.JSONField(default=dict, blank=True)
    secret_data_encrypted = models.TextField(blank=True)
    last_error = models.TextField(blank=True)
    last_checked_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["tenant", "kind"], name="uniq_tenant_integration"),
        ]
        ordering = ["tenant_id", "kind"]

    def __str__(self) -> str:
        return f"{self.tenant.slug}: {self.kind}"


class JobRun(models.Model):
    class JobType(models.TextChoices):
        PIPELINE = "pipeline", "Pipeline"
        AMOCRM_SYNC = "amocrm_sync", "amoCRM Sync"
        RADIST_SYNC = "radist_sync", "Radist Sync"
        AI_ANALYZE = "ai_analyze", "AI Analyze"
        REPORT_BUILD = "report_build", "Report Build"
        TELEGRAM_NOTIFY = "telegram_notify", "Telegram Notify"

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        RUNNING = "running", "Running"
        SUCCESS = "success", "Success"
        FAILED = "failed", "Failed"

    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE)
    job_type = models.CharField(max_length=30, choices=JobType.choices)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    current_step = models.CharField(max_length=120, blank=True)
    progress = models.PositiveSmallIntegerField(default=0)
    error = models.TextField(blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.tenant.slug}: {self.job_type} ({self.status})"


class Report(models.Model):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        READY = "ready", "Ready"
        SENT = "sent", "Sent"
        FAILED = "failed", "Failed"

    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE)
    period_start = models.DateField()
    period_end = models.DateField()
    report_type = models.CharField(max_length=40, default="daily")
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.DRAFT)
    summary_text = models.TextField(blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    data_ref = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-period_end"]

    def __str__(self) -> str:
        return f"{self.tenant.slug}: {self.report_type} {self.period_start} - {self.period_end}"


class ReportMessage(models.Model):
    report = models.ForeignKey(Report, on_delete=models.CASCADE, related_name="messages")
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True
    )
    question = models.TextField()
    answer = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at"]

    def __str__(self) -> str:
        return f"Report {self.report_id}: {self.question[:40]}"


class AuditLog(models.Model):
    tenant = models.ForeignKey(Tenant, on_delete=models.SET_NULL, null=True, blank=True)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="audit_logs",
    )
    action = models.CharField(max_length=120)
    message = models.TextField(blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        actor_label = str(self.actor) if self.actor else "system"
        return f"{actor_label}: {self.action}"
