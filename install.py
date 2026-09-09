#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""token-waterline 安装器：
1. 备份 ~/.zcode/cli/config.json
2. 幂等合并 hooks（UserPromptSubmit + SessionStart[resume|compact]，process 型，无 shell 依赖）
3. 安装 /waterline 斜杠命令到 ~/.zcode/commands/（ENGINE 路径按实际位置替换）
4. 写入默认阈值配置 ~/.zcode/token-waterline.json（已存在则不动）
"""
import json
import os
import shutil
import sys
import time

HOME = os.path.expanduser("~")
HERE = os.path.dirname(os.path.abspath(__file__))
ENGINE = os.path.join(HERE, "bin", "waterline.py")
CFG = os.path.join(HOME, ".zcode", "cli", "config.json")
CMD_SRC = os.path.join(HERE, "commands", "waterline.md")
CMD_DST = os.path.join(HOME, ".zcode", "commands", "waterline.md")
THRESH = os.path.join(HOME, ".zcode", "token-waterline.json")
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


def hook_entry(matcher=None):
    entry = {
        "hooks": [{
            "type": "process",
            "command": sys.executable,
            "args": [ENGINE, "--format", "line", "--hook"],
            "timeoutMs": 10000,
            "statusMessage": MARK,
        }]
    }
    if matcher:
        entry["matcher"] = matcher
    return entry


def prune(entries):
    return [e for e in entries
            if not any(h.get("statusMessage") == MARK for h in e.get("hooks", []))]


def main():
    # ---- 1. 备份
    if os.path.exists(CFG):
        bak = CFG + ".bak-waterline-" + time.strftime("%Y%m%d-%H%M%S")
        shutil.copy2(CFG, bak)
        print(f"[1/4] 已备份原配置 → {bak}")
        with open(CFG, encoding="utf-8") as f:
            cfg = json.load(f)
    else:
        bak = None
        cfg = {}
        print("[1/4] 未找到原配置，将新建 " + CFG)

    # ---- 2. 合并 hooks（幂等）
    hooks = cfg.setdefault("hooks", {})
    hooks["enabled"] = True
    hooks.setdefault("timeoutMs", 10000)
    events = hooks.setdefault("events", {})
    events["UserPromptSubmit"] = prune(events.get("UserPromptSubmit", [])) + [hook_entry()]
    events["SessionStart"] = prune(events.get("SessionStart", [])) + [hook_entry("resume|compact")]
    with open(CFG, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    print(f"[2/4] hooks 已写入 {CFG}（UserPromptSubmit + SessionStart[resume|compact]）")

    # ---- 3. 安装斜杠命令
    with open(CMD_SRC, encoding="utf-8") as f:
        body = f.read().replace("{{ENGINE}}", ENGINE)
    os.makedirs(os.path.dirname(CMD_DST), exist_ok=True)
    with open(CMD_DST, "w", encoding="utf-8") as f:
        f.write(body)
    print(f"[3/4] 斜杠命令已安装 → {CMD_DST}（/waterline，源文件含路径占位符已替换）")

    # ---- 4. 阈值配置
    if os.path.exists(THRESH):
        print(f"[4/4] 阈值配置已存在，保留：{THRESH}")
    else:
        with open(THRESH, "w", encoding="utf-8") as f:
            json.dump(DEFAULT_THRESHOLDS, f, ensure_ascii=False, indent=2)
        print(f"[4/4] 默认阈值已写入：{THRESH}")

    if bak is None:
        print("提示：首次安装且此前无 config.json，卸载时可直接删除该文件。")
    print("完成。新开的 ZCode 会话即生效；已有会话是否热加载取决于 ZCode 实现（重启会话最稳）。")


if __name__ == "__main__":
    main()
