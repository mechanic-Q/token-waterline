#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""token-waterline 卸载器（插件形态）。

1. 从 ~/.zcode/cli/config.json 的 plugins.dirs 中移除本仓库目录
2. 清理任何残留的 token-waterline hooks（旧版遗留）
3. 删除用户级命令 ~/.zcode/commands/waterline.md（若存在）

不动阈值配置与调试目录，如需彻底清理按提示手动删除。
"""
import json
import os
import sys

HOME = os.path.expanduser("~")
HERE = os.path.dirname(os.path.abspath(__file__))
CFG = os.path.join(HOME, ".zcode", "cli", "config.json")
LEGACY_CMD = os.path.join(HOME, ".zcode", "commands", "waterline.md")
MARK = "token-waterline"


def is_ours(entry):
    return any(h.get("statusMessage") == MARK for h in entry.get("hooks", []))


def prune(entries):
    return [e for e in entries if not is_ours(e)]


def main():
    if os.path.exists(CFG):
        with open(CFG, encoding="utf-8") as f:
            cfg = json.load(f)

        changed = False

        # 1. 取消插件注册
        plugins = cfg.get("plugins", {})
        dirs = plugins.get("dirs", [])
        if HERE in dirs:
            dirs.remove(HERE)
            changed = True
            print(f"已从 plugins.dirs 移除 {HERE}")
        enabled = plugins.get("enabledPlugins", {})
        for key in list(enabled):
            if key.startswith("qianliyan@"):
                del enabled[key]
                changed = True
                print(f"已移除 enabledPlugins 中的 {key}")

        # 2. 清理残留 hooks
        events = cfg.get("hooks", {}).get("events", {})
        for name in list(events):
            before = len(events[name])
            events[name] = prune(events[name])
            if before != len(events[name]):
                changed = True
                print(f"已清理 hooks.{name} 中的 token-waterline 条目")
            if not events[name]:
                del events[name]

        if changed:
            with open(CFG, "w", encoding="utf-8") as f:
                json.dump(cfg, f, ensure_ascii=False, indent=2)
        else:
            print("配置中未发现 token-waterline 相关内容，无需改动")

    # 3. 删除旧版用户级命令
    if os.path.exists(LEGACY_CMD):
        os.remove(LEGACY_CMD)
        print(f"已删除 {LEGACY_CMD}")

    print("\n卸载完成。重开 ZCode 会话后生效。")
    print("阈值配置与调试目录未动，可手动删除：")
    print(f"  {os.path.join(HOME, '.zcode', 'token-waterline.json')}")
    print(f"  {os.path.join(HOME, '.zcode', 'token-waterline-debug')}")


if __name__ == "__main__":
    sys.exit(main())
