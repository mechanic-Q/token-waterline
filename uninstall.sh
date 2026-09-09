#!/usr/bin/env bash
# token-waterline 卸载入口（实际逻辑在 uninstall.py）
set -euo pipefail
cd "$(dirname "$0")"
exec python uninstall.py
