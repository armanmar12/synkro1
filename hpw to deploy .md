# Synkro: How to Deploy (Production)

Ниже рабочий сценарий деплоя для сервера `93.170.72.31` и папки `/opt/synkro/app`.

## 1) Подготовка локально

1. Проверь, что нужные файлы изменены локально.
2. Проверь Django локально:

```powershell
python server\manage.py check
python server\manage.py makemigrations --check --dry-run
```

Если `makemigrations --check` показывает изменения, сначала создай и добавь миграцию.

## 2) Копирование на сервер

Копируй измененные файлы в `root@93.170.72.31:/opt/synkro/app/`.

Пример:

```powershell
scp requirements.txt root@93.170.72.31:/opt/synkro/app/
scp server\core\views.py root@93.170.72.31:/opt/synkro/app/server/core/
scp server\core\models.py root@93.170.72.31:/opt/synkro/app/server/core/
scp server\core\migrations\0004_userprofile.py root@93.170.72.31:/opt/synkro/app/server/core/migrations/
scp server\core\templates\core\base.html root@93.170.72.31:/opt/synkro/app/server/core/templates/core/
```

Важно: шаблоны должны копироваться именно в `server/core/templates/core/`, миграции в `server/core/migrations/`.

## 3) Сборка и перезапуск

```bash
ssh root@93.170.72.31
cd /opt/synkro/app
docker compose up -d --build web worker beat
```

## 4) Проверка после деплоя

```bash
cd /opt/synkro/app
docker compose ps
docker compose logs --tail=120 web
docker compose exec -T web python server/manage.py showmigrations core
```

Быстрый HTTP smoke-check:

```bash
curl -I http://127.0.0.1/login/
curl -I http://127.0.0.1/dashboard/
```

## 5) Что должно быть в норме

- `docker compose ps`: `web`, `worker`, `beat`, `db`, `redis`, `caddy` в статусе `Up`.
- В логах `web` нет ошибок миграций.
- `showmigrations` показывает примененные новые миграции (`[X]`).
- `/login/` открывается.

## 6) Частые проблемы

1. Миграция не применяется:
- Причина: файл миграции скопирован не в `server/core/migrations/`.
- Решение: перемести файл и пересобери:

```bash
cd /opt/synkro/app
docker compose up -d --build web worker beat
```

2. Изменения шаблона не видны:
- Причина: шаблон попал не в `server/core/templates/core/`.
- Решение: скопировать в правильную директорию и пересобрать `web`.

3. Откат на прошлую версию:
- Если есть бэкап файлов/репозиторий, верни прошлые файлы и снова:

```bash
cd /opt/synkro/app
docker compose up -d --build web worker beat
```

