#!/usr/bin/bash

set -e

export PYTHONPATH="$HOME/vitamova-admin:$PYTHONPATH"

echo "=== Restarting Vitamova Admin web service ==="

# Step 1: Kill Gunicorn if it's running
echo "🔧 Stopping Gunicorn if running..."
if pgrep gunicorn > /dev/null; then
  pkill gunicorn
  echo "✅ Gunicorn stopped."
else
  echo "ℹ️ Gunicorn was not running."
fi

# Step 2: Remove old project
if [ -d vitamova-admin ]; then
  echo "🗑️ Removing old vitamova-admin directory..."
  sudo rm -rf vitamova-admin
  echo "✅ Old vitamova-admin directory removed."
fi

# Step 3: Clone the latest code
echo "⬇️ Cloning latest vitamova-admin repo..."
if git clone -b dev https://github.com/vitamova/vitamova-admin.git; then
  echo "✅ Git clone successful."
else
  echo "❌ ERROR: Failed to clone repo."
  exit 1
fi

# Step 4: Fix script permissions
echo "🔐 Setting script permissions..."
chmod 755 ~/vitamova-admin/deploy/initiate-server.sh ~/vitamova-admin/deploy/start-services.sh ~/vitamova-admin/deploy/restart-web.sh
echo "✅ Permissions set."

# Step 5: Install Python dependencies
echo "📦 Installing Python dependencies..."
source ~/vitamova-admin-venv/bin/activate
pip install -r ~/vitamova-admin/deploy/requirements.txt
deactivate
echo "✅ Python dependencies installed."

# Step 6: Apply the django migrations
echo "🔄 Applying Django migrations..."
source ~/vitamova-admin-venv/bin/activate
export PYTHONPATH="$HOME/vitamova-admin:$PYTHONPATH"
cd ~/vitamova-admin/webapp
python3 manage.py migrate
if [ $? -ne 0 ]; then
  echo "❌ ERROR: Django migrations failed."
  exit 1
fi
deactivate
cd ~/vitamova-admin
echo "✅ Migrations applied successfully."

# Step 7: Start services
echo "🚀 Starting Vitamova services..."
export PYTHONPATH="$HOME/vitamova-admin:$PYTHONPATH"
if bash ~/vitamova-admin/deploy/start-services.sh; then
  echo "✅ Vitamova services started successfully."
else
  echo "❌ ERROR: Failed to start Vitamova services."
  exit 1
fi

echo "🎉 Restart complete."