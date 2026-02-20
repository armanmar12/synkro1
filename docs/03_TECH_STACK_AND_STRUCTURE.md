# Synkro: Стек и структура репозитория

Обновлено: 2026-02-20
Статус: живой документ

## 1. Технологический стек
- Python 3.12 (в Docker-образе `python:3.12-slim`)
- Django 5
- Django REST Framework
- PostgreSQL 16
- Redis 7
- Celery 5
- Gunicorn
- WhiteNoise (статические файлы)
- Caddy 2

Зависимости: `requirements.txt`.

## 2. Структура и назначение папок
## 2.1 Ядро проекта (обязательно)
- `server/`
  - Django-проект (`synkro`) и приложение (`core`)
  - модели, вьюхи, формы, шаблоны, статика, миграции
  - production pipeline: `core/pipeline.py`, `core/tasks.py`, `core/connectors.py`
  - runtime-наблюдаемость запусков: `JobRun` + `JobRunEvent` (модели/админка/UI)
- `deploy/`
  - `entrypoint.sh` (migrate + collectstatic + gunicorn)
  - `Caddyfile` (reverse proxy/TLS)
  - инструкции деплоя
- `supabase/`
  - SQL-миграции для клиентского слоя данных
- `scripts/`
  - служебные скрипты интеграций/выгрузок (PowerShell)

## 2.2 Файлы корня (обязательно)
- `docker-compose.yml` - состав контейнеров.
- `Dockerfile` - сборка runtime-образа.
- `requirements.txt` - Python-зависимости.
- `.env.example` - шаблон окружения.
- `.gitignore` - правила игнора.
- `how2.md` - короткая шпаргалка команд и базового процесса деплоя.

## 2.3 CI/CD
- `.github/workflows/deploy.yml` - деплой на VPS через GitHub Actions (push в `main`).

## 2.4 Документация (новая точка входа)
- `docs/` - единая и актуализируемая документация по проекту.

## 2.5 Временные/исследовательские данные
- `temp/` - только для временных, тестовых и отладочных артефактов.

Правило:
- в `server/`, `deploy/`, `supabase/`, `scripts/` не кладем временные выгрузки;
- все dump/json/probe-выгрузки складываем в `temp/`.

## 3. Контейнеры в `docker-compose.yml`
- `db` - PostgreSQL
- `redis` - Redis
- `web` - Django/Gunicorn
- `worker` - Celery worker
- `beat` - Celery beat
- `caddy` - reverse proxy + TLS

## 4. Конфигурация окружения (минимум)
- `DJANGO_SECRET_KEY`
- `DJANGO_DEBUG`
- `DJANGO_ALLOWED_HOSTS`
- `DB_NAME`, `DB_USER`, `DB_PASSWORD`, `DB_HOST`, `DB_PORT`
- `REDIS_URL` / `CELERY_BROKER_URL` / `CELERY_RESULT_BACKEND`
- `INTEGRATION_SECRET_KEY` (рекомендуется явно задавать в production)
