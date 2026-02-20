# Deploy Synkro to VPS (Ubuntu 24.04) with Docker + Caddy

This repo includes a Docker Compose stack:
- Django (gunicorn)
- Celery worker + beat
- Postgres + Redis
- Caddy reverse proxy (HTTPS via Let's Encrypt)

## 1) Server prerequisites (run on VPS)
```bash
sudo apt update
sudo apt install -y ca-certificates curl gnupg git

sudo install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
sudo chmod a+r /etc/apt/keyrings/docker.gpg

echo \
  "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu \
  $(. /etc/os-release && echo $VERSION_CODENAME) stable" | \
  sudo tee /etc/apt/sources.list.d/docker.list > /dev/null

sudo apt update
sudo apt install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
sudo systemctl enable --now docker
```

## 2) Copy project to VPS
Recommended location: `/opt/synkro/app`.

## 3) Configure Caddy and env
Edit:
- `deploy/Caddyfile`:
  - set your email
  - set hostname (now: `vds28824.vpsza500.kz`)

Create `.env` near `docker-compose.yml`:
```env
DJANGO_SECRET_KEY=change-me-long-random
DJANGO_DEBUG=0
DJANGO_ALLOWED_HOSTS=vds28824.vpsza500.kz

DB_NAME=synkro
DB_USER=synkro
DB_PASSWORD=change-me
```

## 4) Start stack
```bash
docker compose up -d --build
docker compose ps
```

Then open:
- `https://vds28824.vpsza500.kz/login/`

