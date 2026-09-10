#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""token-waterline 安装器（插件形态）。

把本仓库注册为 ZCode 的本地（inline）插件：
1. 备份 ~/.zcode/cli/config.json
2. 在 plugins.dirs 中加入本仓库目录 → 所有新会话自动加载插件的 hooks 与 /waterline 命令
3. 清理旧版（v0.1.x）写入用户级 config.json 的 token-waterline hooks，避免双重注入
4. 删除旧版装在 ~/.zcode/commands/waterline.md 的用户级命令（由插件命令接管）
5. 写入默认阈值配置 ~/.zcode/token-waterline.json（已存在则保留）

幂等：可重复运行。
"""
import json
import os
import shutil
import sys
import time

HOME = os.path.expanduser("~")
HERE = os.path.dirname(os.path.abspath(__file__))
CFG = os.path.join(HOME, ".zcode", "cli", "config.json")
LEGACY_CMD = os.path.join(HOME, ".zcode", "commands", "waterline.md")
THRESH = os.path.join(HOME, ".zcode", "token-waterline.json")
PATH_FILE = os.path.join(HOME, ".zcode", "token-waterline.path")
ENGINE = os.path.join(HERE, "bin", "waterline.py")
MARK = "token-waterline"

DEFAULT_THRESHOLDS = {
    "warn": 70,
    "alert": 85,
    "critical": 95,
    "inject": "always",
    "default_context_limit": 1000000,
    "model_limits": {},
    "debug": False,
}


def is_ours(entry):
    return any(h.get("statusMessage") == MARK for h in entry.get("hooks", []))


def prune(entries):
    return [e for e in entries if not is_ours(e)]


def main():
    # ---- 1. 备份
    if os.path.exists(CFG):
        with open(CFG, encoding="utf-8") as f:
            cfg = json.load(f)
        bak = CFG + ".bak-waterline-" + time.strftime("%Y%m%d-%H%M%S")
        shutil.copy2(CFG, bak)
        print(f"[1/5] 已备份原配置 → {bak}")
    else:
        cfg, bak = {}, None
        print("[1/5] 未找到原配置，将新建 " + CFG)

    # ---- 2. 注册插件目录
    plugins = cfg.setdefault("plugins", {})
    plugins.setdefault("enabled", True)
    dirs = plugins.setdefault("dirs", [])
    if HERE not in dirs:
        dirs.append(HERE)
    with open(CFG, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    print(f"[2/5] 已注册插件目录 → plugins.dirs += {HERE}")

    # ---- 3. 清理旧式 hooks（避免与新插件的 hooks 双重注入）
    hooks = cfg.get("hooks", {})
    events = hooks.get("events", {}) if isinstance(hooks, dict) else {}
    removed = 0
    for name in list(events):
        before = len(events[name])
        events[name] = prune(events[name])
        removed += before - len(events[name])
        if not events[name]:
            del events[name]
    if removed:
        with open(CFG, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
        print(f"[3/5] 已清理 {removed} 条旧版用户级 hooks（改由插件提供）")
    else:
        print("[3/5] 无旧版 hooks 需要清理")

    # ---- 4. 移除旧版用户级命令（插件命令接管）
    if os.path.exists(LEGACY_CMD):
        os.remove(LEGACY_CMD)
        try:
            os.rmdir(os.path.dirname(LEGACY_CMD))  # 目录空了就删
        except OSError:
            pass
        print(f"[4/5] 已移除旧版用户级命令 → {LEGACY_CMD}")
    else:
        print("[4/5] 无旧版命令需要移除")

    # ---- 5. 阈值配置 + 引擎路径文件（供 /waterline 命令定位）
    if os.path.exists(THRESH):
        print(f"[5/5] 阈值配置已存在，保留：{THRESH}")
    else:
        with open(THRESH, "w", encoding="utf-8") as f:
            json.dump(DEFAULT_THRESHOLDS, f, ensure_ascii=False, indent=2)
        print(f"[5/5] 默认阈值已写入：{THRESH}")
    with open(PATH_FILE, "w", encoding="utf-8") as f:
        f.write(os.path.abspath(ENGINE))
    print(f"      引擎路径已记录 → {PATH_FILE}")

    print()
    print("完成。插件 'qianliyan@inline' 已启用，包含：")
    print("  • hooks：每轮 UserPromptSubmit + SessionStart(resume|compact) 自动注入水位")
    print("  • 命令：/waterline（自动出现在命令菜单）")
    print()
    print("⚠ hooks 与命令是会话启动时加载的 —— 请**重开一个 ZCode 会话**生效。")
    if bak is None:
        print("提示：首次安装且此前无 config.json，卸载时可直接删除该文件。")


if __name__ == "__main__":
    sys.exit(main())
