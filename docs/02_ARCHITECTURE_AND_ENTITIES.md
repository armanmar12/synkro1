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
4. Фоновая задача формирует отчет по цепочке интеграций в production-режиме.
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

## 3.5 TenantRuntimeConfig
- Runtime-настройки tenant pipeline.
- Поля: `mode`, `timezone`, `business_day_start`, `scheduled_run_time`, `is_schedule_enabled`.
- Ограничения ручного запуска: `max_force_lookback_days`, `max_force_window_hours`.
- Параметры потока: `radist_fetch_limit`, `min_dialogs_for_report`, `telegram_followup_minutes`.

## 3.6 JobRun
- Трекер выполнения задач/пайплайнов.
- Поля: `job_type`, `mode`, `trigger_type`, `status`, `current_step`, `progress`, `error`, `window_start`, `window_end`, `idempotency_key`, временные метки.

## 3.7 Report
- Отчет за период.
- Поля: `period_start`, `period_end`, `window_start`, `window_end`, `report_type`, `status`, `summary_text`, `metadata`, `data_ref`, `followup_deadline_at`.

## 3.8 ReportMessage
- Follow-up вопросы/ответы к отчету.

## 3.9 AuditLog
- Журнал действий и событий.
- Поля: `action`, `message`, `metadata`, `ip_address`.

## 3.10 JobRunEvent
- Журнал шагов конкретного запуска `JobRun` для наблюдаемости.
- Поля: `job_run`, `level`, `step`, `message`, `metadata`, `created_at`.
- Используется в UI отчетов для отображения прогресса и причин сбоев при ручном/плановом запуске.

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
- Нет полного API-слоя для внешнего управления отчетами.
- Нет выделенного мониторинга/алертинга уровня SRE (метрики, централизованный сбор логов, on-call нотификации).
- Не оформлен публичный контракт версионирования runtime-конфига tenant для внешних инструментов.
