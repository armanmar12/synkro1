#!/usr/bin/env sh
set -eu

attempts=0
max_attempts=20
until python server/manage.py migrate --noinput; do
  attempts=$((attempts + 1))
  if [ "$attempts" -ge "$max_attempts" ]; then
    echo "DB not ready after ${max_attempts} attempts"
    exit 1
  fi
  sleep 2
done

python server/manage.py collectstatic --noinput

exec gunicorn synkro.wsgi:application \
  --chdir server \
  --bind 0.0.0.0:8000 \
  --workers 3 \
  --timeout 120
