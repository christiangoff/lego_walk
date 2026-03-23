#!/bin/bash
# Pull latest code, migrate DB, and restart the service.
# Usage: ./update.sh

set -e

echo "Pulling latest code..."
git pull

echo "Installing/updating dependencies..."
venv/bin/pip install -r requirements.txt -q

echo "Running migrations..."
venv/bin/python3 migrate.py

echo "Restarting service..."
sudo systemctl restart lego_walk

echo "Done. Status:"
sudo systemctl status lego_walk --no-pager
