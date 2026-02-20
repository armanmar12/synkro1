# Synkro: Архитектура и сущности

Обновлено: 2026-02-20
Статус: живой документ

## 1. Архитектура (MVP)
- Backend/UI: Django (templates).
- БД приложения: PostgreSQL (через Django ORM).
- Очереди и фоновые задачи: Celery + Redis.
- Reverse proxy/TLS: Caddy.
- Внешние интеграции: Supabase, amoCRM (read-only), Radist, AI provider, Telegram.

## 2. Основной поток данных
1. Пользователь настраивает интеграции в `dashboard/settings`.
2. Секреты интеграций шифруются и хранятся в `IntegrationConfig.secret_data_encrypted`.
3. Проверки интеграций вызывают внешние API с сервера.
4. (Целевая логика) Фоновая задача формирует отчет по цепочке интеграций.
5. Результаты записываются в отчеты/хранилище данных и доступны в UI.

## 3. Сущности центральной БД (Django models)
Источник: `server/core/models.py`

## 3.1 Tenant
- Компания/клиент в системе.
- Ключевые поля: `name`, `slug`, `status`, `timezone`.

## 3.2 UserRole
- Роль пользователя в контексте tenant или глобально.
- Роли: `super_admin`, `admin_lite`, `user`.

## 3.3 UserProfile
- Профиль пользователя.
- Поля: `phone`, `timezone`.

## 3.4 IntegrationConfig
- Конфиги интеграций на tenant.
- Виды интеграций: `supabase`, `amocrm`, `radist`, `ai`, `telegram`.
- Поля состояния: `status`, `last_error`, `last_checked_at`.
- Секретные поля: `secret_data_encrypted` (шифрование через Fernet, см. `server/core/crypto.py`).

## 3.5 JobRun
- Трекер выполнения задач/пайплайнов.
- Поля: `job_type`, `status`, `current_step`, `progress`, `error`, временные метки.

## 3.6 Report
- Отчет за период.
- Поля: `period_start`, `period_end`, `report_type`, `status`, `summary_text`, `metadata`, `data_ref`.

## 3.7 ReportMessage
- Follow-up вопросы/ответы к отчету.

## 3.8 AuditLog
- Журнал действий и событий.
- Поля: `action`, `message`, `metadata`, `ip_address`.

## 4. Авторизация и доступ
Источник: `server/core/views.py`

- Поддерживается auth Django user.
- Есть временный fallback-логин из `.env` (`TEMP_LOGIN_USER`, `TEMP_LOGIN_PASSWORD`).
- Доступ к настройкам:
  - `superuser` всегда имеет доступ;
  - иначе по `UserRole` (только `super_admin` и `admin_lite`).

## 5. Важные инварианты
- amoCRM: только чтение, без бизнес-записи обратно.
- Секреты интеграций не должны отображаться в UI после сохранения.
- Все проверки интеграций выполняются на сервере.

## 6. Что еще не завершено
- Нет полного production-оркестратора пайплайна внутри Django/Celery.
- Нет полного API-слоя для внешнего управления отчетами.
- Не оформлен отдельный модуль доменной логики по каждому интегратору (сейчас часть проверки в `views.py`).

