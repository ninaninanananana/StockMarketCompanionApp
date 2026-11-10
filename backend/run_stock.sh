#!/bin/zsh
# 由 crontab 呼叫。使用專案 venv，並切換到 backend 目錄（.env 以相對路徑載入）。
BACKEND_DIR="${0:A:h}"
cd "$BACKEND_DIR" || exit 1
exec "$BACKEND_DIR/venv/bin/python" scheduler.py
