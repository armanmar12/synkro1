# Synkro: поэтапный план и работа на сервере

Этот документ описывает пошаговый план внедрения и конкретные команды для сервера.

## 1) Общий план разработки (по этапам)

### Этап A — База данных сервиса (наша центральная БД)
Цель: хранить пользователей, роли, настройки интеграций, логи.
- Модели: Tenant, UserRole, IntegrationConfig, JobRun, Report, AuditLog.
- Админка Django: управление пользователями/тенантами/ключами.
- Хранение секретов: ключи только на сервере, шифрованно.

Критерий готовности:
- Можно создать tenant и пользователя через админку.
- Можно сохранить/обновить ключи интеграции (без утечки в UI).

### Этап B — Временный логин (готово)
- Сейчас используется временный логин/пароль из `.env`.
- Это временно, до внедрения нормальной auth.

### Этап C — Settings Wizard (Supabase)
Цель: рабочий шаг “Supabase” в настройках.
- Поля: URL, anon, service role.
- Проверка подключения.
- Генерация SQL схемы для Supabase.
- Сохранение статуса.

Критерий:
- Ввод ключей сохраняется.
- Проверка возвращает “ОК”.

### Этап D — amoCRM
Цель: подключение к amoCRM (read-only).
- OAuth (callback URL).
- Получение лидов, links, контактов.
- Тестовые 3 диалога по данным контактов.

### Этап E — Radist
Цель: подтянуть диалоги по телефонам.
- API key.
- chats/messages.
- запись в Supabase.

### Этап F — Gemini (AI)
Цель: анализ 3 тестовых диалогов.
- Проверка ключа.
- Выбор модели.
- Тестовый анализ.

### Этап G — Telegram
Цель: отправка тестового отчета.
- token + chat_id.
- тестовое сообщение.

### Этап H — Принудительный отчет
Цель: полный пайплайн с мониторингом.
- amo → Supabase → Radist → Supabase → AI → Supabase → Telegram.
- индикаторы шагов.

## 2) Работа на сервере (runbook)

### 2.1 Проверка состояния
```bash
cd /opt/synkro/app
docker compose ps
```

### 2.2 Перезапуск
```bash
cd /opt/synkro/app
docker compose down
docker compose up -d --build
```

### 2.3 Логи
```bash
cd /opt/synkro/app
docker compose logs --tail=100 web
docker compose logs --tail=100 worker
docker compose logs --tail=100 beat
docker compose logs --tail=100 caddy
```

### 2.4 Проверка доступности
```bash
curl -I http://127.0.0.1/login/
curl -I http://93.170.72.31/login/
```

### 2.5 Обновление кода (через scp)
На Windows:
```powershell
scp .\requirements.txt root@93.170.72.31:/opt/synkro/app/
scp .\server\synkro\settings.py root@93.170.72.31:/opt/synkro/app/server/synkro/
scp .\server\core\views.py root@93.170.72.31:/opt/synkro/app/server/core/
```

### 2.6 Переключение на HTTPS после домена
Когда домен начнет резолвиться:
1. Обновить `deploy/Caddyfile` под новый домен.
2. Перезапустить `caddy`:
```bash
cd /opt/synkro/app
docker compose restart caddy
```

## 3) Что сейчас работает
- Каркас UI.
- Временный логин (логин/пароль из `.env`).
- Запуск в Docker.

## 4) Что делаем дальше
Следующий шаг — внедрить модели и админку, чтобы “Настройки” стали реальными.

