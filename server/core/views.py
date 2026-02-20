import json
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from django.conf import settings
from django.contrib import auth
from django.shortcuts import redirect, render
from django.utils import timezone

from .crypto import decrypt_payload, encrypt_payload
from .forms import (
    AISettingsForm,
    AmoCRMSettingsForm,
    RadistSettingsForm,
    SupabaseSettingsForm,
    TelegramSettingsForm,
    UserProfileForm,
)
from .models import IntegrationConfig, Tenant, UserProfile, UserRole


def _is_authed(request):
    return request.user.is_authenticated or request.session.get("temp_auth") is True


def _require_auth(view_func):
    def _wrapped(request, *args, **kwargs):
        if not _is_authed(request):
            return redirect("login")
        return view_func(request, *args, **kwargs)

    return _wrapped


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
    return render(
        request,
        "core/dashboard_reports.html",
        {
            "active": "reports",
            "can_access_settings_menu": _can_access_settings_menu(request.user),
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
    message = None

    supabase_form = SupabaseSettingsForm()
    amocrm_form = AmoCRMSettingsForm()
    radist_form = RadistSettingsForm()
    ai_form = AISettingsForm()
    telegram_form = TelegramSettingsForm()

    has_secret_supabase = False
    has_secret_amocrm = False
    has_secret_radist = False
    has_secret_ai = False
    has_secret_telegram = False

    can_manage_settings = _can_manage_settings(request.user, tenant)
    if tenant and can_manage_settings:
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

        action = request.POST.get("action")
        if request.method == "POST" and action:
            if action == "save_supabase" or action == "check_supabase":
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
                    radist_config.secret_data_encrypted = encrypt_payload(
                        {"api_key": radist_form.cleaned_data["api_key"].strip()}
                    )
                    if action == "check_radist":
                        ok, error = _check_radist(
                            radist_config.public_config.get("api_base_url", ""),
                            radist_config.public_config.get("company_id"),
                            decrypt_payload(radist_config.secret_data_encrypted).get("api_key", ""),
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

            elif action in {"save_ai", "check_ai"}:
                ai_form = AISettingsForm(request.POST)
                if ai_form.is_valid():
                    ai_config.public_config = {
                        "provider": ai_form.cleaned_data["provider"].strip(),
                        "model": ai_form.cleaned_data["model"].strip(),
                        "prompt": ai_form.cleaned_data["prompt"].strip(),
                    }
                    ai_config.secret_data_encrypted = encrypt_payload(
                        {"api_key": ai_form.cleaned_data["api_key"].strip()}
                    )
                    if action == "check_ai":
                        ok, error = _check_ai(
                            ai_config.public_config.get("provider", ""),
                            ai_config.public_config.get("model", ""),
                            decrypt_payload(ai_config.secret_data_encrypted).get("api_key", ""),
                        )
                        ai_config.status = (
                            IntegrationConfig.Status.OK
                            if ok
                            else IntegrationConfig.Status.ERROR
                        )
                        ai_config.last_error = "" if ok else error
                        ai_config.last_checked_at = timezone.now()
                        message = "AI: проверка OK" if ok else f"AI: ошибка {error}"
                    else:
                        ai_config.status = IntegrationConfig.Status.PENDING
                        message = "AI: настройки сохранены."
                    ai_config.save()
                    has_secret_ai = True
                else:
                    message = "AI: проверьте заполнение полей."

            elif action in {"save_telegram", "check_telegram"}:
                telegram_form = TelegramSettingsForm(request.POST)
                if telegram_form.is_valid():
                    telegram_config.public_config = {
                        "chat_id": telegram_form.cleaned_data["chat_id"].strip()
                    }
                    telegram_config.secret_data_encrypted = encrypt_payload(
                        {"bot_token": telegram_form.cleaned_data["bot_token"].strip()}
                    )
                    if action == "check_telegram":
                        ok, error = _check_telegram(
                            telegram_config.public_config.get("chat_id", ""),
                            decrypt_payload(telegram_config.secret_data_encrypted).get("bot_token", ""),
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
        if request.method != "POST" or action not in {"save_ai", "check_ai"}:
            ai_form = AISettingsForm(
                initial={
                    "provider": ai_config.public_config.get("provider", ""),
                    "model": ai_config.public_config.get("model", ""),
                    "prompt": ai_config.public_config.get("prompt", ""),
                }
            )
        if request.method != "POST" or action not in {"save_telegram", "check_telegram"}:
            telegram_form = TelegramSettingsForm(
                initial={
                    "chat_id": telegram_config.public_config.get("chat_id", ""),
                }
            )

    context = {
        "active": "settings",
        "can_access_settings_menu": _can_access_settings_menu(request.user),
        "tenant": tenant,
        "tenants": tenants,
        "can_manage_settings": can_manage_settings,
        "message": message,
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


def _check_ai(provider: str, model: str, api_key: str) -> tuple[bool, str]:
    if not provider:
        return False, "provider обязателен"
    if not api_key:
        return False, "API key обязателен"

    if provider.lower() == "openai":
        endpoint = "https://api.openai.com/v1/models"
        req = Request(
            endpoint,
            headers={"Authorization": f"Bearer {api_key}", "User-Agent": "synkro/1.0"},
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

    return False, "Провайдер пока не поддержан для проверки"


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
