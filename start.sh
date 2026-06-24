#!/usr/bin/env bash
# Start EcoMonitor AQ: backend (Flask + live pipeline) + localhost.run tunnel.
# Usage: ./start.sh
set -e
cd "$(dirname "$0")"

PORT="${PORT:-4173}"
KEY="$HOME/.ssh/id_ed25519"

echo "==> Stopping any old backend/tunnel..."
lsof -ti "tcp:$PORT" | xargs kill 2>/dev/null || true
pkill -f "localhost.run" 2>/dev/null || true
sleep 1

echo "==> Starting backend on :$PORT ..."
( cd backend && nohup python3 server.py > /tmp/bali_backend.log 2>&1 & )
sleep 4

echo "==> Starting tunnel ..."
# Using your SSH key. Once the key is registered at https://admin.localhost.run/
# this gives the SAME (permanent) subdomain every time.
nohup ssh -o StrictHostKeyChecking=no -o ServerAliveInterval=30 -i "$KEY" \
  -R 80:localhost:"$PORT" localhost.run > /tmp/lhr_tunnel.log 2>&1 &

for i in $(seq 1 15); do
  URL=$(grep -oE "https://[a-z0-9-]+\.(lhr\.life|localhost\.run)" /tmp/lhr_tunnel.log | tail -1)
  [ -n "$URL" ] && break
  sleep 2
done

echo ""
echo "==> Dashboard live at: ${URL:-(cek /tmp/lhr_tunnel.log)}"
echo "    Backend log : /tmp/bali_backend.log"
echo "    Tunnel log  : /tmp/lhr_tunnel.log"
echo "    Status      : curl ${URL}/api/status"
