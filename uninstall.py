#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""token-waterline 卸载器：移除本工具写入的 hooks 与斜杠命令。
不动阈值配置（~/.zcode/token-waterline.json）和调试目录，如需彻底清理请手动删除。"""
import json
import os

HOME = os.path.expanduser("~")
CFG = os.path.join(HOME, ".zcode", "cli", "config.json")
CMD_DST = os.path.join(HOME, ".zcode", "commands", "waterline.md")
MARK = "token-waterline"


def prune(entries):
    return [e for e in entries
            if not any(h.get("statusMessage") == MARK for h in e.get("hooks", []))]


def main():
    if os.path.exists(CFG):
        with open(CFG, encoding="utf-8") as f:
            cfg = json.load(f)
        events = cfg.get("hooks", {}).get("events", {})
        for name in list(events):
            events[name] = prune(events[name])
            if not events[name]:
                del events[name]
        with open(CFG, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
        print(f"已从 {CFG} 移除 token-waterline hooks")
    if os.path.exists(CMD_DST):
        os.remove(CMD_DST)
        print(f"已删除 {CMD_DST}")
    print("卸载完成。阈值配置与调试目录未动，可手动删除：")
    print(f"  {os.path.join(HOME, '.zcode', 'token-waterline.json')}")
    print(f"  {os.path.join(HOME, '.zcode', 'token-waterline-debug')}")


if __name__ == "__main__":
    main()
