#!/usr/bin/bash

set -e

LOG_DIR=~/vitamova-admin/logs
ACCESS_LOG=$LOG_DIR/gunicorn-access.log
ERROR_LOG=$LOG_DIR/gunicorn-error.log
SETTINGS_FILE=~/vitamova-admin/webapp/webapp/settings.py

echo "=== Activating virtual environment ==="
source ~/vitamova-admin-venv/bin/activate

echo "=== Navigating to Django project ==="
cd ~/vitamova-admin/webapp

#echo "=== Setting DEBUG = False in settings.py ==="
#sed -i 's/^DEBUG = True/DEBUG = False/' "$SETTINGS_FILE"

echo "=== Creating log directory if it doesn't exist ==="
mkdir -p "$LOG_DIR"

echo "=== Starting Django with Gunicorn (logging enabled) ==="
gunicorn webapp.wsgi:application \
  --chdir ~/vitamova-admin/webapp \
  --bind 127.0.0.1:8888 \
  --workers 3 \
  --access-logfile "$ACCESS_LOG" \
  --error-logfile "$ERROR_LOG" \
  --daemon

echo "✅ Gunicorn started on localhost:8888"
echo "📄 Access Log: $ACCESS_LOG"
echo "📄 Error Log:  $ERROR_LOG"