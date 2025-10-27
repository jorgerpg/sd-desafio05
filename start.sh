#!/usr/bin/env bash
set -euo pipefail
cd /app
python3 "server.py" &
cd "static"
python3 -m http.server 8080