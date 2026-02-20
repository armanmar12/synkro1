# Synkro: Запуск, деплой, Git и журнал изменений

Обновлено: 2026-02-20
Статус: живой документ

## 1. Локальный запуск (без Docker)
Предусловия:
- Python 3.12+
- доступ к Postgres/Redis (или fallback-конфиг для локальной разработки)

Команды:
```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python server\manage.py migrate
python server\manage.py runserver
```

Проверка:
- `http://127.0.0.1:8000/login/`
- `http://127.0.0.1:8000/health/`

## 2. Локальный/серверный запуск через Docker Compose
```bash
docker compose up -d --build
docker compose ps
docker compose logs --tail=100 web
```

Полезно:
```bash
docker compose logs --tail=100 worker
docker compose logs --tail=100 beat
docker compose logs --tail=100 caddy
```

## 3. Production деплой (текущий контур)
Основной документ: `deploy/README_DEPLOY_VPS.md`.

Базовая схема:
1. Скопировать репозиторий на VPS (`/opt/synkro/app`).
2. Заполнить `.env`.
3. Проверить `deploy/Caddyfile`.
4. Выполнить `docker compose up -d --build`.
5. Проверить `/login/` и `/health/`.

## 4. Работа с GitHub
Рекомендуемый процесс:
1. Создать ветку под задачу.
2. Внести изменения.
3. Прогнать проверки (`manage.py check`, smoke tests).
4. Коммит + push ветки.
5. Pull Request в `main`.

Минимальные команды:
```bash
git checkout -b feature/<short-name>
git add .
git commit -m "feat: <summary>"
git push -u origin feature/<short-name>
```

## 5. Правило для временных файлов
- Любые временные файлы и отладочные выгрузки создаются только в `temp/`.
- После завершения работ `temp/` можно безопасно очищать по запросу.

## 6. Регламент обновления документации
При каждом изменении проекта обновляем минимум:
- `docs/01_BUSINESS_AND_PROCESSES.md` (если меняется бизнес-логика);
- `docs/02_ARCHITECTURE_AND_ENTITIES.md` (если меняются модели/процессы/права);
- `docs/03_TECH_STACK_AND_STRUCTURE.md` (если меняется стек или структура);
- текущий файл: секцию `Журнал изменений`.

## 7. Журнал изменений
Формат записи:
- `YYYY-MM-DD | Тип изменения | Что изменено | Файлы`

Записи:
- `2026-02-20 | docs/bootstrap | Создан единый комплект документации и правила ее ведения | docs/01_BUSINESS_AND_PROCESSES.md, docs/02_ARCHITECTURE_AND_ENTITIES.md, docs/03_TECH_STACK_AND_STRUCTURE.md, docs/04_RUN_DEPLOY_GIT_AND_CHANGELOG.md`
- `2026-02-20 | docs/master-map | Добавлен единый входной документ по сервису | docs/SERVICE_MASTER.md`
- `2026-02-20 | repo/cleanup | Тестовые каталоги и старые черновики вынесены в temp/, дефолтные пути скрипта обновлены | scripts/push_deals_to_supabase.ps1, .gitignore`
- `2026-02-20 | docs/integration-canon | Зафиксирован канонический пайплайн amoCRM -> Radist -> Supabase -> AI -> Telegram с инвариантами anti-dup и tenant-изоляцией | docs/05_INTEGRATION_PIPELINE_CANONICAL.md`
- `2026-02-20 | cicd/github-only | Добавлен автодеплой через GitHub Actions и короткая шпаргалка команд | .github/workflows/deploy.yml, how2.md`
- `2026-02-20 | runtime/pipeline-prod-base | Добавлены TenantRuntimeConfig, idempotent JobRun, Celery scheduler tick, реальный асинхронный запуск report pipeline, ручной запуск по временному окну и обновленный UI настроек/отчетов | server/core/models.py, server/core/migrations/0005_jobrun_attempt_jobrun_idempotency_key_and_more.py, server/core/pipeline.py, server/core/tasks.py, server/core/views.py, server/core/forms.py, server/core/templates/core/dashboard_settings.html, server/core/templates/core/dashboard_reports.html, server/synkro/celery.py`
- `2026-02-20 | runtime/live-connectors | Добавлен боевой сбор данных из amoCRM/Radist и upsert в Supabase внутри pipeline worker (режимы amocrm_radist/radist_only/amocrm_only), затем построение отчета на актуальном слое данных | server/core/connectors.py, server/core/pipeline.py`
