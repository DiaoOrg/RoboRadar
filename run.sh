#!/usr/bin/env bash
# 前台启动 RoboRadar 并打开浏览器：./run.sh [端口]
cd "$(dirname "$0")"
PY="${ROBORADAR_PYTHON:-$HOME/miniconda3/envs/roboradar/bin/python}"
exec "$PY" -m roboradar serve --port "${1:-8770}" --open
