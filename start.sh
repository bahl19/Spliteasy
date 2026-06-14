#!/bin/bash
# Start SplitEasy app with Cloudflare Tunnel

# Kill existing
pkill -f "python3 /opt/data/split-easy/app.py" 2>/dev/null
pkill -f "cloudflared" 2>/dev/null
sleep 1

# Start app
cd /opt/data/split-easy
export PORT=8080
nohup python3 /opt/data/split-easy/app.py > /tmp/spliteasy.log 2>&1 &
echo "App PID: $!"

sleep 2

# Start tunnel
nohup /tmp/cloudflared tunnel --url http://localhost:8080 --no-autoupdate > /tmp/tunnel.log 2>&1 &
echo "Tunnel PID: $!"

sleep 5

# Extract URL
grep -oP 'https://[a-z0-9-]+\.trycloudflare\.com' /tmp/tunnel.log | tail -1
