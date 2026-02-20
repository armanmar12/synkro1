import json
from datetime import timedelta
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from django.conf import settings
from django.contrib import auth
from django.db.models import Q
from django.shortcuts import redirect, render
from django.utils import timezone

from .crypto import decrypt_payload, encrypt_payload
from .forms import (
    AISettingsForm,
    AmoCRMSettingsForm,
    ForcedReportForm,
    RadistSettingsForm,
    ReportFollowupForm,
    SupabaseSettingsForm,
    TenantRuntimeSettingsForm,
    TelegramSettingsForm,
    UserProfileForm,
)
from .models import (
    IntegrationConfig,
    JobRun,
    JobRunEvent,
    Report,
    ReportMessage,
    Tenant,
    TenantRuntimeConfig,
    UserProfile,
    UserRole,
)
from .followups import build_report_followup_answer
from .pipeline import (
    PipelineError,
    build_job_idempotency_key,
    compute_last_closed_window,
    get_or_create_runtime_config,
    queue_report_job,
    validate_forced_window,
)


def _is_authed(request):
    return request.user.is_authenticated or request.session.get("temp_auth") is True


def _require_auth(view_func):
    def _wrapped(request, *args, **kwargs):
        if not _is_authed(request):
            return redirect("login")
        return view_func(request, *args, **kwargs)

    return _wrapped


def _model_choices_from_public(raw_models) -> list[tuple[str, str]]:
    if not isinstance(raw_models, list):
        return []

    model_choices: list[tuple[str, str]] = []
    for item in raw_models:
        if not isinstance(item, str):
            continue
        model_name = item.strip()
        if not model_name:
            continue
        model_choices.append((model_name, model_name.replace("models/", "")))
    return model_choices


def login_view(request):
    context = {}
    if request.method == "POST":
        username = request.POST.get("username", "").strip()
        password = request.POST.get("password", "").strip()
        user = auth.authenticate(request, username=username, password=password)
        if user:
            auth.login(request, user)
            return redirect("dashboard_overview")
        if username == settings.TEMP_LOGIN_USER and password == settings.TEMP_LOGIN_PASSWORD:
            request.session["temp_auth"] = True
            return redirect("dashboard_overview")
        context["error"] = "Неверный логин или пароль"
    return render(request, "core/login.html", context)


def logout_view(request):
    if request.user.is_authenticated:
        auth.logout(request)
    request.session.flush()
    return redirect("login")


@_require_auth
def dashboard_overview(request):
    return render(
        request,
        "core/dashboard_overview.html",
        {
            "active": "overview",
            "can_access_settings_menu": _can_access_settings_menu(request.user),
        },
    )


@_require_auth
def dashboard_reports(request):
    tenant_id = request.POST.get("tenant_id") or request.GET.get("tenant")
    tenant = None
    if tenant_id:
        tenant = Tenant.objects.filter(id=tenant_id).first()
    if tenant is None:
        tenant = Tenant.objects.filter(slug="globalfruit").first()
    if tenant is None:
        tenant = Tenant.objects.order_by("name").first()

    tenants = list(Tenant.objects.order_by("name"))
    runtime_config = get_or_create_runtime_config(tenant) if tenant else None
    can_force_report = _can_run_reports_as_client(request.user, tenant)
    message = None

    forced_form = ForcedReportForm()
    action = request.POST.get("action")
    if request.method == "POST" and action == "run_forced_report":
        forced_form = ForcedReportForm(request.POST)
        if not tenant or not runtime_config:
            message = "Tenant not found."
        elif not can_force_report:
            message = "Forced reports can be started only from a client account."
        elif not forced_form.is_valid():
            message = "Check forced report date range."
        else:
            window_start = forced_form.cleaned_data["window_start"]
            window_end = forced_form.cleaned_data["window_end"]
            if timezone.is_naive(window_start):
                window_start = timezone.make_aware(window_start, timezone.get_current_timezone())
            if timezone.is_naive(window_end):
                window_end = timezone.make_aware(window_end, timezone.get_current_timezone())
            validation_error = validate_forced_window(runtime_config, window_start, window_end)
            if validation_error:
                message = validation_error
            else:
                active_job = (
                    JobRun.objects.filter(
                        tenant=tenant,
                        job_type=JobRun.JobType.REPORT_BUILD,
                        status__in=[JobRun.Status.PENDING, JobRun.Status.RUNNING],
                    )
                    .order_by("-created_at")
                    .first()
                )
                if active_job:
                    message = (
                        f"Active report job already exists. Job #{active_job.id}. "
                        "Stop current job or wait for completion."
                    )
                else:
                    idempotency_key = build_job_idempotency_key(
                        tenant.id,
                        JobRun.TriggerType.MANUAL,
                        runtime_config.mode,
                        window_start,
                        window_end,
                    )
                    try:
                        job, created = queue_report_job(
                            tenant=tenant,
                            runtime_config=runtime_config,
                            trigger_type=JobRun.TriggerType.MANUAL,
                            window_start=window_start,
                            window_end=window_end,
                            requested_by=request.user,
                            idempotency_key=idempotency_key,
                            metadata={"source": "dashboard_reports"},
                        )
                        if created:
                            message = f"Forced report queued. Job #{job.id}."
                        else:
                            message = f"Matching job already exists. Job #{job.id}."
                    except PipelineError as exc:
                        message = str(exc)
    elif request.method == "POST" and action == "stop_report_job":
        if not tenant:
            message = "Tenant not found."
        elif not can_force_report:
            message = "Stop is allowed only from a client account."
        else:
            job_id = request.POST.get("job_id")
            job = (
                JobRun.objects.filter(
                    id=job_id,
                    tenant=tenant,
                    job_type=JobRun.JobType.REPORT_BUILD,
                )
                .order_by("-created_at")
                .first()
            )
            if not job:
                message = "Job not found."
            elif job.status not in [JobRun.Status.PENDING, JobRun.Status.RUNNING]:
                message = f"Job #{job.id} already finished with status '{job.status}'."
            else:
                revoked = _stop_report_job(job, request.user)
                if revoked:
                    message = f"Job #{job.id} stopped and Celery task revoked."
                else:
                    message = f"Job #{job.id} marked as stopped."

    if (request.method != "POST" or action == "stop_report_job") and runtime_config:
        default_end = timezone.now()
        default_start = default_end - timedelta(hours=min(runtime_config.max_force_window_hours, 24))
        forced_form = ForcedReportForm(
            initial={
                "window_start": default_start.strftime("%Y-%m-%dT%H:%M"),
                "window_end": default_end.strftime("%Y-%m-%dT%H:%M"),
            }
        )

    active_report_job = None
    recent_jobs = []
    job_events = []
    log_job = None
    last_scheduled_window = None
    reports = []
    if tenant and runtime_config:
        recent_jobs = list(
            JobRun.objects.filter(tenant=tenant, job_type=JobRun.JobType.REPORT_BUILD)
            .order_by("-created_at")[:10]
        )
        now_ts = timezone.now()
        for job in recent_jobs:
            stalled_minutes = None
            if job.status == JobRun.Status.RUNNING and job.updated_at:
                delta = now_ts - job.updated_at
                if delta.total_seconds() >= 300:
                    stalled_minutes = int(delta.total_seconds() // 60)
            job.stalled_minutes = stalled_minutes

        active_report_job = (
            JobRun.objects.filter(
                tenant=tenant,
                job_type=JobRun.JobType.REPORT_BUILD,
                status__in=[JobRun.Status.PENDING, JobRun.Status.RUNNING],
            )
            .order_by("-created_at")
            .first()
        )

        selected_job_id = request.GET.get("job") or request.POST.get("job_id")
        if selected_job_id:
            log_job = (
                JobRun.objects.filter(
                    id=selected_job_id,
                    tenant=tenant,
                    job_type=JobRun.JobType.REPORT_BUILD,
                )
                .first()
            )
        if not log_job:
            log_job = (
                JobRun.objects.filter(
                    tenant=tenant,
                    job_type=JobRun.JobType.REPORT_BUILD,
                    status=JobRun.Status.RUNNING,
                )
                .order_by("-updated_at")
                .first()
            )
        if not log_job and recent_jobs:
            log_job = recent_jobs[0]
        if log_job:
            job_events = list(
                JobRunEvent.objects.filter(job_run=log_job).order_by("created_at")[:200]
            )

        window_start, window_end = compute_last_closed_window(runtime_config)
        last_scheduled_window = {
            "window_start": window_start,
            "window_end": window_end,
            "mode": runtime_config.mode,
        }
        reports = list(Report.objects.filter(tenant=tenant).order_by("-created_at")[:20])

    return render(
        request,
        "core/dashboard_reports.html",
        {
            "active": "reports",
            "can_access_settings_menu": _can_access_settings_menu(request.user),
            "message": message,
            "tenant": tenant,
            "tenants": tenants,
            "runtime_config": runtime_config,
            "can_force_report": can_force_report,
            "forced_form": forced_form,
            "last_scheduled_window": last_scheduled_window,
            "active_report_job": active_report_job,
            "recent_jobs": recent_jobs,
            "log_job": log_job,
            "job_events": job_events,
            "reports": reports,
        },
    )


@_require_auth
def report_detail(request, report_id: int):
    report = (
        Report.objects.select_related("tenant", "job_run")
        .filter(id=report_id)
        .first()
    )
    if not report:
        return redirect("dashboard_reports")

    message = None
    followup_form = ReportFollowupForm()
    if request.method == "POST":
        followup_form = ReportFollowupForm(request.POST)
        if followup_form.is_valid():
            question = followup_form.cleaned_data["question"].strip()
            history = list(
                report.messages.order_by("-created_at")
                .values_list("question", "answer")[:6]
            )
            history.reverse()
            try:
                answer = build_report_followup_answer(
                    report=report,
                    question=question,
                    history=history,
                )
                ReportMessage.objects.create(
                    report=report,
                    actor=request.user if request.user.is_authenticated else None,
                    question=question,
                    answer=answer,
                )
                message = "AI follow-up saved."
                followup_form = ReportFollowupForm()
            except PipelineError as exc:
                message = f"AI follow-up error: {exc}"
        else:
            message = "Please check your question."

    report_messages = list(report.messages.select_related("actor").order_by("created_at")[:50])
    tenant_id = request.GET.get("tenant") or report.tenant_id
    return render(
        request,
        "core/report_detail.html",
        {
            "active": "reports",
            "can_access_settings_menu": _can_access_settings_menu(request.user),
            "report": report,
            "report_messages": report_messages,
            "followup_form": followup_form,
            "message": message,
            "tenant_id": tenant_id,
        },
    )


@_require_auth
def dashboard_profile(request):
    if not request.user.is_authenticated:
        return redirect("dashboard_overview")

    profile, _ = UserProfile.objects.get_or_create(user=request.user)
    message = None

    if request.method == "POST":
        form = UserProfileForm(request.POST)
        if form.is_valid():
            request.user.email = form.cleaned_data["email"].strip()
            request.user.save(update_fields=["email"])
            profile.phone = form.cleaned_data["phone"].strip()
            profile.timezone = form.cleaned_data["timezone"].strip()
            profile.save(update_fields=["phone", "timezone", "updated_at"])
            message = "Профиль сохранен."
    else:
        form = UserProfileForm(
            initial={
                "email": request.user.email,
                "phone": profile.phone,
                "timezone": profile.timezone,
            }
        )

    return render(
        request,
        "core/dashboard_profile.html",
        {
            "active": "profile",
            "message": message,
            "form": form,
            "can_access_settings_menu": _can_access_settings_menu(request.user),
        },
    )


@_require_auth
def dashboard_settings(request):
    if not _can_access_settings_menu(request.user):
        if request.user.is_authenticated:
            return redirect("dashboard_profile")
        return redirect("dashboard_overview")

    tenant_id = request.POST.get("tenant_id") or request.GET.get("tenant")
    tenant = None
    if tenant_id:
        tenant = Tenant.objects.filter(id=tenant_id).first()
    if tenant is None:
        tenant = Tenant.objects.filter(slug="globalfruit").first()
    if tenant is None:
        tenant = Tenant.objects.order_by("name").first()

    tenants = list(Tenant.objects.order_by("name"))
    supabase_config = None
    amocrm_config = None
    radist_config = None
    ai_config = None
    telegram_config = None
    runtime_config = None
    message = None

    ai_model_choices: list[tuple[str, str]] = []
    supabase_form = SupabaseSettingsForm()
    amocrm_form = AmoCRMSettingsForm()
    radist_form = RadistSettingsForm()
    ai_form = AISettingsForm(model_choices=ai_model_choices)
    telegram_form = TelegramSettingsForm()
    runtime_form = TenantRuntimeSettingsForm()

    has_secret_supabase = False
    has_secret_amocrm = False
    has_secret_radist = False
    has_secret_ai = False
    has_secret_telegram = False
    show_amocrm_settings = True
    show_radist_settings = True

    can_manage_settings = _can_manage_settings(request.user, tenant)
    if tenant and can_manage_settings:
        runtime_config = get_or_create_runtime_config(tenant)
        show_amocrm_settings = runtime_config.mode in {
            TenantRuntimeConfig.Mode.AMOCRM_RADIST,
            TenantRuntimeConfig.Mode.AMOCRM_ONLY,
        }
        show_radist_settings = runtime_config.mode in {
            TenantRuntimeConfig.Mode.AMOCRM_RADIST,
            TenantRuntimeConfig.Mode.RADIST_ONLY,
        }

        supabase_config = _get_or_create_config(tenant, IntegrationConfig.Kind.SUPABASE)
        amocrm_config = _get_or_create_config(tenant, IntegrationConfig.Kind.AMOCRM)
        radist_config = _get_or_create_config(tenant, IntegrationConfig.Kind.RADIST)
        ai_config = _get_or_create_config(tenant, IntegrationConfig.Kind.AI)
        telegram_config = _get_or_create_config(tenant, IntegrationConfig.Kind.TELEGRAM)

        supabase_secret = decrypt_payload(supabase_config.secret_data_encrypted)
        amocrm_secret = decrypt_payload(amocrm_config.secret_data_encrypted)
        radist_secret = decrypt_payload(radist_config.secret_data_encrypted)
        ai_secret = decrypt_payload(ai_config.secret_data_encrypted)
        telegram_secret = decrypt_payload(telegram_config.secret_data_encrypted)

        has_secret_supabase = bool(supabase_secret.get("service_role_key"))
        has_secret_amocrm = bool(
            amocrm_secret.get("access_token")
            or amocrm_secret.get("client_secret")
            or amocrm_secret.get("refresh_token")
        )
        has_secret_radist = bool(radist_secret.get("api_key"))
        has_secret_ai = bool(ai_secret.get("api_key"))
        has_secret_telegram = bool(telegram_secret.get("bot_token"))
        ai_model_choices = _model_choices_from_public(ai_config.public_config.get("available_models", []))

        action = request.POST.get("action")
        if request.method == "POST" and action:
            if action == "save_runtime":
                runtime_form = TenantRuntimeSettingsForm(request.POST)
                if runtime_form.is_valid():
                    runtime_config.mode = runtime_form.cleaned_data["mode"]
                    runtime_config.timezone = runtime_form.cleaned_data["timezone"].strip()
                    runtime_config.business_day_start = runtime_form.cleaned_data["business_day_start"]
                    runtime_config.scheduled_run_time = runtime_form.cleaned_data["scheduled_run_time"]
                    runtime_config.is_schedule_enabled = runtime_form.cleaned_data["is_schedule_enabled"]
                    runtime_config.radist_fetch_limit = runtime_form.cleaned_data["radist_fetch_limit"]
                    runtime_config.min_dialogs_for_report = runtime_form.cleaned_data[
                        "min_dialogs_for_report"
                    ]
                    runtime_config.max_force_lookback_days = runtime_form.cleaned_data[
                        "max_force_lookback_days"
                    ]
                    runtime_config.max_force_window_hours = runtime_form.cleaned_data[
                        "max_force_window_hours"
                    ]
                    runtime_config.telegram_followup_minutes = runtime_form.cleaned_data[
                        "telegram_followup_minutes"
                    ]
                    runtime_config.save()
                    show_amocrm_settings = runtime_config.mode in {
                        TenantRuntimeConfig.Mode.AMOCRM_RADIST,
                        TenantRuntimeConfig.Mode.AMOCRM_ONLY,
                    }
                    show_radist_settings = runtime_config.mode in {
                        TenantRuntimeConfig.Mode.AMOCRM_RADIST,
                        TenantRuntimeConfig.Mode.RADIST_ONLY,
                    }
                    message = "Runtime settings saved."
                else:
                    message = "Runtime settings: please check required fields."

            elif action == "save_supabase" or action == "check_supabase":
                supabase_form = SupabaseSettingsForm(request.POST)
                if supabase_form.is_valid():
                    url = supabase_form.cleaned_data["supabase_url"].strip()
                    anon = supabase_form.cleaned_data["supabase_anon_key"].strip()
                    service_key = supabase_form.cleaned_data["supabase_service_role_key"].strip()

                    supabase_config.public_config = {"url": url, "anon_key": anon}
                    if service_key:
                        supabase_config.secret_data_encrypted = encrypt_payload(
                            {"service_role_key": service_key}
                        )
                        has_secret_supabase = True

                    if action == "check_supabase":
                        ok, error = _check_supabase(url, anon)
                        supabase_config.status = (
                            IntegrationConfig.Status.OK
                            if ok
                            else IntegrationConfig.Status.ERROR
                        )
                        supabase_config.last_error = "" if ok else error
                        supabase_config.last_checked_at = timezone.now()
                        message = "Supabase: проверка OK" if ok else f"Supabase: ошибка {error}"
                    else:
                        supabase_config.status = IntegrationConfig.Status.PENDING
                        message = "Supabase: настройки сохранены."

                    supabase_config.save()
                else:
                    message = "Supabase: проверьте заполнение полей."

            elif action in {"save_amocrm", "check_amocrm"}:
                amocrm_form = AmoCRMSettingsForm(request.POST)
                if amocrm_form.is_valid():
                    domain = amocrm_form.cleaned_data["domain"].strip()
                    amocrm_config.public_config = {
                        "domain": domain,
                        "client_id": amocrm_form.cleaned_data["client_id"].strip(),
                    }
                    secret_payload = amocrm_secret.copy()
                    access_token = amocrm_form.cleaned_data["access_token"].strip()
                    if access_token:
                        secret_payload["access_token"] = access_token
                    client_secret = amocrm_form.cleaned_data["client_secret"].strip()
                    if client_secret:
                        secret_payload["client_secret"] = client_secret
                    refresh_token = amocrm_form.cleaned_data["refresh_token"].strip()
                    if refresh_token:
                        secret_payload["refresh_token"] = refresh_token
                    amocrm_config.secret_data_encrypted = encrypt_payload(secret_payload)
                    if action == "check_amocrm":
                        ok, error = _check_amocrm(domain, secret_payload.get("access_token", ""))
                        amocrm_config.status = (
                            IntegrationConfig.Status.OK
                            if ok
                            else IntegrationConfig.Status.ERROR
                        )
                        amocrm_config.last_error = "" if ok else error
                        amocrm_config.last_checked_at = timezone.now()
                        message = "amoCRM: проверка OK" if ok else f"amoCRM: ошибка {error}"
                    else:
                        amocrm_config.status = IntegrationConfig.Status.PENDING
                        message = "amoCRM: настройки сохранены."
                    amocrm_config.save()
                    has_secret_amocrm = True
                else:
                    message = "amoCRM: проверьте заполнение полей."

            elif action in {"save_radist", "check_radist"}:
                radist_form = RadistSettingsForm(request.POST)
                if radist_form.is_valid():
                    radist_config.public_config = {
                        "api_base_url": radist_form.cleaned_data["api_base_url"].strip(),
                        "company_id": radist_form.cleaned_data["company_id"],
                    }
                    secret_payload = radist_secret.copy()
                    api_key = radist_form.cleaned_data["api_key"].strip()
                    if api_key:
                        secret_payload["api_key"] = api_key
                    active_api_key = secret_payload.get("api_key", "")
                    if not active_api_key:
                        radist_form.add_error("api_key", "Укажите API key или сохраните его ранее.")
                        message = "Radist: API key обязателен."
                    else:
                        radist_config.secret_data_encrypted = encrypt_payload(secret_payload)
                    if active_api_key:
                        if action == "check_radist":
                            ok, error = _check_radist(
                                radist_config.public_config.get("api_base_url", ""),
                                radist_config.public_config.get("company_id"),
                                active_api_key,
                            )
                            radist_config.status = (
                                IntegrationConfig.Status.OK
                                if ok
                                else IntegrationConfig.Status.ERROR
                            )
                            radist_config.last_error = "" if ok else error
                            radist_config.last_checked_at = timezone.now()
                            message = "Radist: проверка OK" if ok else f"Radist: ошибка {error}"
                        else:
                            radist_config.status = IntegrationConfig.Status.PENDING
                            message = "Radist: настройки сохранены."
                        radist_config.save()
                        has_secret_radist = True
                else:
                    message = "Radist: проверьте заполнение полей."

            elif action in {"save_ai", "check_ai", "load_ai_models"}:
                ai_form = AISettingsForm(request.POST, model_choices=ai_model_choices)
                if ai_form.is_valid():
                    secret_payload = ai_secret.copy()
                    api_key = ai_form.cleaned_data["api_key"].strip()
                    if api_key:
                        secret_payload["api_key"] = api_key
                    active_api_key = secret_payload.get("api_key", "")
                    ai_config.public_config = {
                        "provider": ai_form.cleaned_data["provider"].strip(),
                        "model": ai_form.cleaned_data["model"].strip(),
                        "profile_name": ai_form.cleaned_data["profile_name"].strip(),
                        "prompt": ai_form.cleaned_data["prompt"].strip(),
                    }
                    if not active_api_key:
                        ai_form.add_error("api_key", "Укажите API key или сохраните его ранее.")
                        message = "AI: API key обязателен."
                    else:
                        models, model_error = _fetch_ai_models(
                            ai_config.public_config.get("provider", ""),
                            active_api_key,
                        )
                        if models:
                            ai_model_choices = models
                            ai_config.public_config["available_models"] = [value for value, _ in models]
                        elif action == "load_ai_models":
                            message = f"AI: не удалось загрузить модели ({model_error})."

                        selected_model = ai_config.public_config.get("model", "")
                        if selected_model:
                            available_values = {value for value, _ in ai_model_choices}
                            if available_values and selected_model not in available_values:
                                ai_config.public_config["model"] = ""

                        ai_config.secret_data_encrypted = encrypt_payload(secret_payload)
                        if action == "check_ai":
                            ok, error = _check_ai(
                                ai_config.public_config.get("provider", ""),
                                ai_config.public_config.get("model", ""),
                                active_api_key,
                            )
                            ai_config.status = (
                                IntegrationConfig.Status.OK
                                if ok
                                else IntegrationConfig.Status.ERROR
                            )
                            ai_config.last_error = "" if ok else error
                            ai_config.last_checked_at = timezone.now()
                            message = "AI: проверка OK" if ok else f"AI: ошибка {error}"
                        elif action == "load_ai_models":
                            ai_config.status = IntegrationConfig.Status.PENDING
                            if models:
                                message = "AI: список моделей загружен."
                        else:
                            ai_config.status = IntegrationConfig.Status.PENDING
                            message = "AI: настройки сохранены."
                        ai_config.save()
                        has_secret_ai = True
                        ai_form = AISettingsForm(
                            initial={
                                "provider": ai_config.public_config.get("provider", "gemini"),
                                "model": ai_config.public_config.get("model", ""),
                                "profile_name": ai_config.public_config.get("profile_name", ""),
                                "prompt": ai_config.public_config.get("prompt", ""),
                            },
                            model_choices=ai_model_choices,
                        )
                else:
                    message = "AI: проверьте заполнение полей."

            elif action in {"save_telegram", "check_telegram"}:
                telegram_form = TelegramSettingsForm(request.POST)
                if telegram_form.is_valid():
                    telegram_config.public_config = {
                        "chat_id": telegram_form.cleaned_data["chat_id"].strip()
                    }
                    secret_payload = telegram_secret.copy()
                    bot_token = telegram_form.cleaned_data["bot_token"].strip()
                    if bot_token:
                        secret_payload["bot_token"] = bot_token
                    active_bot_token = secret_payload.get("bot_token", "")
                    if not active_bot_token:
                        telegram_form.add_error(
                            "bot_token", "Укажите bot token или сохраните его ранее."
                        )
                        message = "Telegram: bot token обязателен."
                    else:
                        telegram_config.secret_data_encrypted = encrypt_payload(secret_payload)
                        if action == "check_telegram":
                            ok, error = _check_telegram(
                                telegram_config.public_config.get("chat_id", ""),
                                active_bot_token,
                            )
                            telegram_config.status = (
                                IntegrationConfig.Status.OK
                                if ok
                                else IntegrationConfig.Status.ERROR
                            )
                            telegram_config.last_error = "" if ok else error
                            telegram_config.last_checked_at = timezone.now()
                            message = "Telegram: проверка OK" if ok else f"Telegram: ошибка {error}"
                        else:
                            telegram_config.status = IntegrationConfig.Status.PENDING
                            message = "Telegram: настройки сохранены."
                        telegram_config.save()
                        has_secret_telegram = True
                else:
                    message = "Telegram: проверьте заполнение полей."

        if request.method != "POST" or action not in {"save_supabase", "check_supabase"}:
            supabase_form = SupabaseSettingsForm(
                initial={
                    "supabase_url": supabase_config.public_config.get("url", ""),
                    "supabase_anon_key": supabase_config.public_config.get("anon_key", ""),
                }
            )
        if request.method != "POST" or action not in {"save_amocrm", "check_amocrm"}:
            amocrm_form = AmoCRMSettingsForm(
                initial={
                    "domain": amocrm_config.public_config.get("domain", ""),
                    "client_id": amocrm_config.public_config.get("client_id", ""),
                }
            )
        if request.method != "POST" or action not in {"save_radist", "check_radist"}:
            radist_form = RadistSettingsForm(
                initial={
                    "api_base_url": radist_config.public_config.get("api_base_url", ""),
                    "company_id": radist_config.public_config.get("company_id", 205113),
                }
            )
        if request.method != "POST" or action not in {"save_ai", "check_ai", "load_ai_models"}:
            ai_form = AISettingsForm(
                initial={
                    "provider": ai_config.public_config.get("provider", "gemini"),
                    "model": ai_config.public_config.get("model", ""),
                    "profile_name": ai_config.public_config.get("profile_name", ""),
                    "prompt": ai_config.public_config.get("prompt", ""),
                },
                model_choices=ai_model_choices,
            )
        if request.method != "POST" or action not in {"save_telegram", "check_telegram"}:
            telegram_form = TelegramSettingsForm(
                initial={
                    "chat_id": telegram_config.public_config.get("chat_id", ""),
                }
            )
        if request.method != "POST" or action not in {"save_runtime"}:
            runtime_form = TenantRuntimeSettingsForm(
                initial={
                    "mode": runtime_config.mode,
                    "timezone": runtime_config.timezone,
                    "business_day_start": runtime_config.business_day_start.strftime("%H:%M"),
                    "scheduled_run_time": runtime_config.scheduled_run_time.strftime("%H:%M"),
                    "is_schedule_enabled": runtime_config.is_schedule_enabled,
                    "radist_fetch_limit": runtime_config.radist_fetch_limit,
                    "min_dialogs_for_report": runtime_config.min_dialogs_for_report,
                    "max_force_lookback_days": runtime_config.max_force_lookback_days,
                    "max_force_window_hours": runtime_config.max_force_window_hours,
                    "telegram_followup_minutes": runtime_config.telegram_followup_minutes,
                }
            )

    context = {
        "active": "settings",
        "can_access_settings_menu": _can_access_settings_menu(request.user),
        "tenant": tenant,
        "tenants": tenants,
        "can_manage_settings": can_manage_settings,
        "message": message,
        "runtime_form": runtime_form,
        "runtime_config": runtime_config,
        "show_amocrm_settings": show_amocrm_settings,
        "show_radist_settings": show_radist_settings,
        "supabase_form": supabase_form,
        "amocrm_form": amocrm_form,
        "radist_form": radist_form,
        "ai_form": ai_form,
        "telegram_form": telegram_form,
        "supabase_status": supabase_config.status
        if supabase_config
        else IntegrationConfig.Status.UNKNOWN,
        "supabase_last_error": supabase_config.last_error if supabase_config else "",
        "amocrm_status": amocrm_config.status if amocrm_config else IntegrationConfig.Status.UNKNOWN,
        "amocrm_last_error": amocrm_config.last_error if amocrm_config else "",
        "radist_status": radist_config.status if radist_config else IntegrationConfig.Status.UNKNOWN,
        "radist_last_error": radist_config.last_error if radist_config else "",
        "ai_status": ai_config.status if ai_config else IntegrationConfig.Status.UNKNOWN,
        "ai_last_error": ai_config.last_error if ai_config else "",
        "telegram_status": telegram_config.status
        if telegram_config
        else IntegrationConfig.Status.UNKNOWN,
        "telegram_last_error": telegram_config.last_error if telegram_config else "",
        "has_secret_supabase": has_secret_supabase,
        "has_secret_amocrm": has_secret_amocrm,
        "has_secret_radist": has_secret_radist,
        "has_secret_ai": has_secret_ai,
        "has_secret_telegram": has_secret_telegram,
    }
    return render(request, "core/dashboard_settings.html", context)


def _check_supabase(url: str, anon_key: str) -> tuple[bool, str]:
    if not url or not anon_key:
        return False, "URL и anon key обязательны."

    endpoint = url.rstrip("/") + "/rest/v1/"
    req = Request(
        endpoint,
        headers={"apikey": anon_key, "Authorization": f"Bearer {anon_key}"},
        method="GET",
    )
    try:
        with urlopen(req, timeout=6) as response:
            if response.status < 400:
                return True, ""
            return False, f"HTTP {response.status}"
    except HTTPError as exc:
        return False, f"HTTP {exc.code}"
    except URLError as exc:
        return False, f"Network error: {exc.reason}"


def _get_or_create_config(tenant: Tenant, kind: str) -> IntegrationConfig:
    config, _ = IntegrationConfig.objects.get_or_create(tenant=tenant, kind=kind)
    return config


def _can_manage_settings(user, tenant: Tenant | None) -> bool:
    if user.is_authenticated and user.is_superuser:
        return True
    if not user.is_authenticated or tenant is None:
        return False
    role = (
        UserRole.objects.filter(user=user, tenant__in=[tenant, None], is_active=True)
        .order_by("tenant_id")
        .first()
    )
    if not role:
        return False
    return role.role in {UserRole.Role.SUPER_ADMIN, UserRole.Role.ADMIN_LITE}


def _can_access_settings_menu(user) -> bool:
    if not user.is_authenticated:
        return False
    if user.is_superuser:
        return True
    return UserRole.objects.filter(
        user=user,
        is_active=True,
        role__in=[UserRole.Role.SUPER_ADMIN, UserRole.Role.ADMIN_LITE],
    ).exists()


def _can_run_reports_as_client(user, tenant: Tenant | None) -> bool:
    if not user.is_authenticated or user.is_superuser or tenant is None:
        return False
    return UserRole.objects.filter(
        user=user,
        is_active=True,
        role=UserRole.Role.USER,
    ).filter(Q(tenant=tenant) | Q(tenant__isnull=True)).exists()


def _stop_report_job(job: JobRun, actor) -> bool:
    metadata = job.metadata or {}
    celery_task_id = str(metadata.get("celery_task_id") or "").strip()
    revoked = False

    if celery_task_id:
        try:
            from synkro.celery import app as celery_app

            celery_app.control.revoke(celery_task_id, terminate=True, signal="SIGTERM")
            revoked = True
        except Exception:
            revoked = False

    job.status = JobRun.Status.FAILED
    job.current_step = "Stopped by user"
    job.error = "Stopped by user."
    job.finished_at = timezone.now()
    job.save(update_fields=["status", "current_step", "error", "finished_at", "updated_at"])

    actor_label = "system"
    if getattr(actor, "is_authenticated", False):
        actor_label = actor.get_username() or str(actor.id)
    JobRunEvent.objects.create(
        job_run=job,
        level=JobRunEvent.Level.WARN,
        message="Stopped by user",
        data={
            "actor": actor_label,
            "celery_task_id": celery_task_id,
            "revoked": revoked,
        },
    )
    return revoked


def _check_amocrm(domain: str, access_token: str) -> tuple[bool, str]:
    if not domain:
        return False, "domain обязателен"
    if not access_token:
        return False, "access_token обязателен для проверки"

    base = domain.strip()
    if not base.startswith("http"):
        base = f"https://{base}"
    endpoint = base.rstrip("/") + "/api/v4/contacts?limit=3"
    req = Request(
        endpoint,
        headers={"Authorization": f"Bearer {access_token}", "User-Agent": "synkro/1.0"},
        method="GET",
    )
    try:
        with urlopen(req, timeout=8) as response:
            if response.status >= 400:
                return False, f"HTTP {response.status}"
            return True, ""
    except HTTPError as exc:
        return False, f"HTTP {exc.code}"
    except URLError as exc:
        return False, f"Network error: {exc.reason}"


def _check_radist(api_base_url: str, company_id: int | None, api_key: str) -> tuple[bool, str]:
    if not api_key:
        return False, "API key обязателен"
    base = (api_base_url or "https://api.radist.online/v2").rstrip("/")
    company = company_id or 205113
    endpoint = f"{base}/companies/{company}/messaging/chats/sources/"
    req = Request(
        endpoint,
        headers={"X-Api-Key": api_key, "User-Agent": "synkro/1.0"},
        method="GET",
    )
    try:
        with urlopen(req, timeout=8) as response:
            if response.status >= 400:
                return False, f"HTTP {response.status}"
            return True, ""
    except HTTPError as exc:
        return False, f"HTTP {exc.code}"
    except URLError as exc:
        return False, f"Network error: {exc.reason}"


def _fetch_ai_models(provider: str, api_key: str) -> tuple[list[tuple[str, str]], str]:
    normalized_provider = (provider or "").strip().lower()
    if not api_key:
        return [], "API key обязателен"

    if normalized_provider in {"gemini", "google", "google_gemini"}:
        endpoint = f"https://generativelanguage.googleapis.com/v1beta/models?key={api_key}"
        req = Request(endpoint, headers={"User-Agent": "synkro/1.0"}, method="GET")
        try:
            with urlopen(req, timeout=8) as response:
                if response.status >= 400:
                    return [], f"HTTP {response.status}"
                payload = json.loads(response.read().decode("utf-8") or "{}")
                model_items = payload.get("models", [])
                result: list[tuple[str, str]] = []
                for item in model_items:
                    model_name = (item.get("name") or "").strip()
                    if not model_name:
                        continue
                    if "gemini" not in model_name.lower():
                        continue
                    result.append((model_name, model_name.replace("models/", "")))
                if not result:
                    return [], "Список моделей пуст"
                return result, ""
        except HTTPError as exc:
            return [], f"HTTP {exc.code}"
        except URLError as exc:
            return [], f"Network error: {exc.reason}"
        except (UnicodeDecodeError, json.JSONDecodeError):
            return [], "Не удалось разобрать ответ AI API"

    if normalized_provider == "openai":
        endpoint = "https://api.openai.com/v1/models"
        req = Request(
            endpoint,
            headers={"Authorization": f"Bearer {api_key}", "User-Agent": "synkro/1.0"},
            method="GET",
        )
        try:
            with urlopen(req, timeout=8) as response:
                if response.status >= 400:
                    return [], f"HTTP {response.status}"
                payload = json.loads(response.read().decode("utf-8") or "{}")
                model_items = payload.get("data", [])
                result: list[tuple[str, str]] = []
                for item in model_items:
                    model_id = (item.get("id") or "").strip()
                    if not model_id:
                        continue
                    result.append((model_id, model_id))
                if not result:
                    return [], "Список моделей пуст"
                return result, ""
        except HTTPError as exc:
            return [], f"HTTP {exc.code}"
        except URLError as exc:
            return [], f"Network error: {exc.reason}"
        except (UnicodeDecodeError, json.JSONDecodeError):
            return [], "Не удалось разобрать ответ AI API"

    return [], "Провайдер пока не поддержан для списка моделей"


def _check_ai(provider: str, model: str, api_key: str) -> tuple[bool, str]:
    if not provider:
        return False, "provider обязателен"
    if not api_key:
        return False, "API key обязателен"

    models, error = _fetch_ai_models(provider, api_key)
    if not models:
        return False, error

    if model:
        available_values = {value for value, _ in models}
        if model not in available_values:
            return False, "Выбранная модель не найдена в списке API"

    return True, ""


def _check_telegram(chat_id: str, bot_token: str) -> tuple[bool, str]:
    if not bot_token or not chat_id:
        return False, "bot token и chat_id обязательны"
    endpoint = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = json.dumps({"chat_id": chat_id, "text": "Synkro: тестовое сообщение"}).encode(
        "utf-8"
    )
    req = Request(
        endpoint,
        headers={"Content-Type": "application/json"},
        data=payload,
        method="POST",
    )
    try:
        with urlopen(req, timeout=8) as response:
            if response.status >= 400:
                return False, f"HTTP {response.status}"
            return True, ""
    except HTTPError as exc:
        return False, f"HTTP {exc.code}"
    except URLError as exc:
        return False, f"Network error: {exc.reason}"
