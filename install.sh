#!/usr/bin/env bash
# token-waterline 安装入口（实际逻辑在 install.py）
set -euo pipefail
cd "$(dirname "$0")"
exec python install.py
