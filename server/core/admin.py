from django.contrib import admin

from .models import (
    AuditLog,
    IntegrationConfig,
    JobRunEvent,
    JobRun,
    Report,
    ReportMessage,
    Tenant,
    TenantRuntimeConfig,
    UserProfile,
    UserRole,
)


@admin.register(Tenant)
class TenantAdmin(admin.ModelAdmin):
    list_display = ("name", "slug", "status", "timezone", "created_at", "updated_at")
    search_fields = ("name", "slug")
    list_filter = ("status",)
    prepopulated_fields = {"slug": ("name",)}
    readonly_fields = ("created_at", "updated_at")


@admin.register(UserRole)
class UserRoleAdmin(admin.ModelAdmin):
    list_display = ("user", "tenant", "role", "is_active", "created_at")
    list_filter = ("role", "is_active")
    search_fields = ("user__username", "user__email", "tenant__name", "tenant__slug")
    readonly_fields = ("created_at",)


@admin.register(UserProfile)
class UserProfileAdmin(admin.ModelAdmin):
    list_display = ("user", "phone", "timezone", "updated_at")
    search_fields = ("user__username", "user__email", "phone", "timezone")
    readonly_fields = ("created_at", "updated_at")


@admin.register(IntegrationConfig)
class IntegrationConfigAdmin(admin.ModelAdmin):
    list_display = ("tenant", "kind", "status", "last_checked_at", "updated_at")
    list_filter = ("kind", "status")
    search_fields = ("tenant__name", "tenant__slug")
    readonly_fields = ("created_at", "updated_at", "last_checked_at")


@admin.register(JobRun)
class JobRunAdmin(admin.ModelAdmin):
    list_display = (
        "tenant",
        "job_type",
        "mode",
        "trigger_type",
        "status",
        "progress",
        "started_at",
        "finished_at",
    )
    list_filter = ("job_type", "mode", "trigger_type", "status")
    search_fields = ("tenant__name", "tenant__slug")
    readonly_fields = ("created_at", "updated_at")


@admin.register(JobRunEvent)
class JobRunEventAdmin(admin.ModelAdmin):
    list_display = ("created_at", "job_run", "level", "message")
    list_filter = ("level",)
    search_fields = ("job_run__tenant__slug", "job_run__tenant__name", "message")
    readonly_fields = ("created_at",)


@admin.register(Report)
class ReportAdmin(admin.ModelAdmin):
    list_display = (
        "tenant",
        "report_type",
        "status",
        "period_start",
        "period_end",
        "followup_deadline_at",
        "updated_at",
    )
    list_filter = ("report_type", "status")
    search_fields = ("tenant__name", "tenant__slug")
    readonly_fields = ("created_at", "updated_at")


@admin.register(TenantRuntimeConfig)
class TenantRuntimeConfigAdmin(admin.ModelAdmin):
    list_display = (
        "tenant",
        "mode",
        "timezone",
        "business_day_start",
        "scheduled_run_time",
        "is_schedule_enabled",
    )
    list_filter = ("mode", "is_schedule_enabled", "timezone")
    search_fields = ("tenant__name", "tenant__slug")
    readonly_fields = ("created_at", "updated_at")


@admin.register(ReportMessage)
class ReportMessageAdmin(admin.ModelAdmin):
    list_display = ("report", "actor", "created_at")
    search_fields = ("report__tenant__name", "report__tenant__slug", "question")
    readonly_fields = ("created_at",)


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = ("created_at", "tenant", "actor", "action")
    list_filter = ("action",)
    search_fields = ("action", "actor__username", "tenant__name", "tenant__slug")
    readonly_fields = ("created_at",)
