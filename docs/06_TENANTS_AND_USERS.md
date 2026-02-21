# Synkro: Tenant + пользователи + роли (как добавлять и связывать)

Обновлено: 2026-02-21
Статус: живой документ

Цель: описать пошагово, как:
1) создать tenant (компанию/клиента),
2) создать пользователя,
3) привязать пользователя к tenant через роль (`UserRole`).

Источник прав и связей: `server/core/models.py` (`Tenant`, `UserRole`, `UserProfile`) и `server/core/views.py` (проверки доступа).

## 1. Термины (как это устроено в коде)
- **Tenant** — компания/клиент (`Tenant`).
- **Пользователь** — стандартный Django user (`AUTH_USER_MODEL`, обычно `auth.User`).
- **Профиль** — доп. данные пользователя (`UserProfile`, связь 1:1 с Django user).
- **Связка user ↔ tenant** — запись в `UserRole`:
  - `user` — пользователь,
  - `tenant` — конкретный tenant **или `NULL`** (глобальная роль),
  - `role` — одна из: `super_admin`, `admin_lite`, `user`,
  - `is_active` — флаг активности.

Инвариант: в базе стоит уникальность `UserRole(user, tenant)` — для одной пары user+tenant может быть только одна запись роли.

## 2. Какие роли на что влияют (MVP)
Текущие проверки (см. `server/core/views.py`):
- `superuser` (Django) — всегда имеет доступ к настройкам.
- `super_admin` / `admin_lite` — дают доступ к **Settings** (на конкретный tenant или глобально через `tenant=NULL`).
- `user` — “клиентская” роль, нужна для ручных действий в **Reports** (принудительный запуск/стоп job) на tenant или глобально через `tenant=NULL`.

Важно: роль с `tenant=NULL` считается глобальной и в проверках применяется ко *всем* tenant.

## 3. Способ №1 (рекомендуемый): через Django Admin UI
Предусловия:
- сервис запущен (локально или на сервере);
- есть Django superuser для входа в админку.

### Шаг 1 — создать superuser (если еще нет)
```powershell
python server\manage.py createsuperuser
```

### Шаг 2 — зайти в админку
Открыть:
- `http://127.0.0.1:8000/admin/` (локально)

### Шаг 3 — создать tenant
1. Admin → **Core → Tenants** → **Add**.
2. Заполнить:
   - `name` (человекочитаемое),
   - `slug` (уникально, используется как tenant-id в Supabase),
   - `status` (обычно `active`),
   - `timezone`.
3. Save.

### Шаг 4 — создать пользователя
1. Admin → **Authentication and Authorization → Users** → **Add**.
2. Заполнить `username` и пароль (и при желании `email`).
3. Save.

Опционально:
- если пользователю нужен доступ в `/admin/`, выставить `staff` (и права), либо `superuser` (очень осторожно).

### Шаг 5 — создать профиль пользователя (рекомендуется)
1. Admin → **Core → User profiles** → **Add**.
2. Выбрать `user`, заполнить `phone`/`timezone`.
3. Save.

### Шаг 6 — связать пользователя с tenant (назначить роль)
1. Admin → **Core → User roles** → **Add**.
2. Заполнить:
   - `user` — созданный пользователь,
   - `tenant` — нужный tenant (или оставить пустым для `NULL` = глобально),
   - `role` — `super_admin` / `admin_lite` / `user`,
   - `is_active` = true.
3. Save.

### Шаг 7 — проверка
1. Залогиниться в UI под созданным пользователем: `http://127.0.0.1:8000/login/`.
2. Проверить доступ:
   - Settings меню видно, если есть активная роль `super_admin` или `admin_lite` (или Django superuser).
   - В Reports кнопка “Принудительный отчет” активна только при клиентской роли `user`.

## 4. Способ №2: через `manage.py shell` (быстро, для окружений без UI)
Пример: создать tenant, пользователя, профиль и роль “user” на конкретный tenant.

```powershell
python server\manage.py shell -c @"
from django.contrib.auth import get_user_model
from core.models import Tenant, UserProfile, UserRole

tenant, _ = Tenant.objects.get_or_create(
    slug='acme',
    defaults={'name': 'ACME', 'timezone': 'Europe/Moscow', 'status': Tenant.Status.ACTIVE},
)

User = get_user_model()
user, created = User.objects.get_or_create(username='acme_user', defaults={'email': 'acme_user@example.com'})
if created:
    user.set_password('CHANGE_ME')
    user.save()

UserProfile.objects.get_or_create(user=user, defaults={'timezone': tenant.timezone})

UserRole.objects.update_or_create(
    user=user,
    tenant=tenant,
    defaults={'role': UserRole.Role.USER, 'is_active': True},
)

print('tenant=', tenant.id, tenant.slug, 'user=', user.id, user.username)
"@
```

Варианты:
- назначить админ-доступ к настройкам tenant: `defaults={'role': UserRole.Role.ADMIN_LITE, ...}`
- сделать глобальную роль: `tenant=None` в `update_or_create(...)` (использовать осторожно).

