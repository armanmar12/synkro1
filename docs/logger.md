# Synkro: Logger

Обновлено: 2026-02-20  
Формат: в этом файле храним только краткое саммари по сделанным изменениям; при следующем обновлении файл перезаписывается актуальным состоянием.

## Сессия 2026-02-20

### 1) Production runtime pipeline
- Внедрен боевой пайплайн отчетов на Celery/Redis.
- Добавлены runtime-настройки tenant: режимы работы, рабочие сутки, расписание, лимиты ручного запуска.
- Добавлены режимы пайплайна:
  - `amocrm_radist`
  - `radist_only`
  - `amocrm_only`
- Реализован сбор данных из amoCRM/Radist и загрузка в Supabase внутри worker-процесса.

### 2) Принудительный отчет и наблюдаемость
- Добавлен `JobRunEvent` для пошагового лога выполнения.
- Принудительный запуск теперь пишет этапы выполнения и ошибки.
- На странице отчетов добавлены:
  - блок последних запусков;
  - лог событий по job;
  - отображение причины падения при ошибке.

### 3) Документация синхронизирована
- Обновлены документы под текущее production-состояние:
  - `docs/01_BUSINESS_AND_PROCESSES.md`
  - `docs/02_ARCHITECTURE_AND_ENTITIES.md`
  - `docs/03_TECH_STACK_AND_STRUCTURE.md`
  - `docs/04_RUN_DEPLOY_GIT_AND_CHANGELOG.md`
  - `docs/05_INTEGRATION_PIPELINE_CANONICAL.md`
  - `docs/SERVICE_MASTER.md`

### 4) Технический статус
- Изменения запушены в `main`.
- Репозиторий чистый (`git status` без локальных изменений).

### 5) Отчеты: автообновление без повторной отправки формы
- Убрано `reload()` после `POST` на странице отчетов, чтобы Firefox не просил подтверждать повторную отправку.
- Автообновление теперь переводится в `GET` с параметром tenant, прогресс job виден без диалога.
- Файлы: `server/core/templates/core/dashboard_reports.html`.

### 6) Отчеты: диагностика зависаний и ручная остановка job
- Добавлен выбор job для просмотра лога (`?tenant=<id>&job=<id>`), теперь видно ошибки и этапы именно нужного запуска.
- В таблице задач показан признак зависания (`Нет обновлений N мин`) для `running` job.
- Добавлена кнопка `Стоп` для `pending/running` job в блоке активной задачи и в таблице.
- Остановка проставляет `FAILED` + событие `Stopped by user` и пытается `revoke` celery task по `celery_task_id`.
- В pipeline сохраняется `celery_task_id` при постановке в очередь.
- В sync-коннекторах добавлены лимиты на объем загрузки (amo leads/contacts, Radist pages/candidates/messages), чтобы снизить риск зависаний на шаге `Syncing source systems`.
- Если sync временно падает, пайплайн продолжает сбор отчета по уже имеющимся данным в Supabase.

### 7) Sync: ускорение по умолчанию
- Ужесточены дефолтные лимиты по объему загрузки из amoCRM/Radist, чтобы отчет гарантированно доходил до сохранения даже при больших объемах.
- Файл: `server/core/connectors.py`.

### 8) Отчеты: веб-карточка, follow-up AI и Telegram по `@`
- Добавлена полноценная карточка отчета в вебе: полный текст, история уточнений и форма вопроса к AI.
- В списке отчетов добавлены действия `Открыть` и `Новое окно`, чтобы отчет можно было смотреть на сайте отдельно от логов.
- Реализован AI follow-up в Telegram: бот отвечает только на сообщения с `@`-упоминанием, остальные сообщения игнорируются.
- Вопросы/ответы follow-up сохраняются в `ReportMessage` как единая история для отчета (веб + Telegram).
- Добавлен фоновый polling `getUpdates` в scheduler tick с хранением `telegram_update_offset` в конфиге интеграции.
- Усилена запись в Supabase `public.reports`: добавлен `source_report_id` (с обратной совместимостью), чтобы несколько отчетов за один день сохранялись отдельными строками.
- Добавлена SQL-миграция Supabase: `supabase/migrations/002_reports_source_report_id.sql`.
- Ключевые файлы: `server/core/followups.py`, `server/core/views.py`, `server/core/templates/core/report_detail.html`, `server/core/pipeline.py`, `server/core/tasks.py`, `server/core/forms.py`, `server/core/urls.py`, `server/core/templates/core/dashboard_reports.html`, `supabase/migrations/002_reports_source_report_id.sql`.
