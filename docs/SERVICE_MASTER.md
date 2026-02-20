# Synkro: SERVICE MASTER (единая точка входа)

Обновлено: 2026-02-20
Статус: основной навигационный документ

## 1. Зачем этот файл
Этот файл - быстрый вход в проект для любой новой сессии.
Если нужно понять проект за 1-2 минуты, начинать отсюда.

## 2. Кратко о системе
Synkro - веб-сервис для B2B-аналитики клиентских коммуникаций.
Текущая реализация построена на Django и включает:
- кабинет пользователя;
- управление интеграциями;
- хранение конфигов и ролей;
- production-пайплайн отчетов с Celery/Redis.

Ключевой инвариант:
- amoCRM только read-only.

## 3. Что уже есть в коде
- Django-проект и UI-страницы (`login`, `dashboard`, `reports`, `profile`, `settings`).
- Центральные модели данных (`Tenant`, `UserRole`, `IntegrationConfig`, `TenantRuntimeConfig`, `JobRun`, `JobRunEvent`, `Report`, `ReportMessage`, `AuditLog`).
- Формы и проверки подключений внешних интеграций из UI.
- Docker-окружение для web/worker/beat/db/redis/caddy.
- Реальный runtime-пайплайн с режимами `amocrm_radist`, `radist_only`, `amocrm_only`.
- Лог шагов запуска и ошибок в UI отчетов для ручного/планового прогона.

## 4. Что сейчас считать ядром репозитория
- `server/`
- `deploy/`
- `supabase/`
- `scripts/`
- `docs/`
- `docker-compose.yml`
- `Dockerfile`
- `requirements.txt`
- `.env.example`
- `.gitignore`

## 5. Что убрано из ядра
Тестовые и исследовательские артефакты перенесены в `temp/`:
- старые API dump/probe каталоги;
- черновые документы.

Правило:
- все временное кладем в `temp/`;
- рабочий код и актуальная документация - только в основных папках.

## 6. Карта документации
1. `docs/01_BUSINESS_AND_PROCESSES.md`
2. `docs/02_ARCHITECTURE_AND_ENTITIES.md`
3. `docs/03_TECH_STACK_AND_STRUCTURE.md`
4. `docs/04_RUN_DEPLOY_GIT_AND_CHANGELOG.md`
5. `docs/05_INTEGRATION_PIPELINE_CANONICAL.md`

## 7. Как поддерживать документы живыми
При любом изменении:
1. Обновить профильный файл (бизнес/архитектура/стек/запуск).
2. Добавить запись в `docs/04_RUN_DEPLOY_GIT_AND_CHANGELOG.md`.
3. Если поменялась структура проекта, обновить этот master-файл.
