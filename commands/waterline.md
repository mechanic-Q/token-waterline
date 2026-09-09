---
description: 显示当前会话的 Token 水位计（上下文占用/累计烧入/剩余轮数）
argument-hint: [burn]
---
运行 Token 水位计并向用户展示结果。

1. 用 Bash 工具执行：`python "{{ENGINE}}" --format gauge`
   （若提示未找到会话，先执行 `python "{{ENGINE}}" --list` 查看最近会话，再用 `--session <id>` 重跑）
2. 把命令输出**原样**转述给用户（保留水位条与数字，不要自行改写或估算）。
3. 若输出含 ⚠ 或 🚨 建议行，向用户明确强调该建议。
4. 若参数 `$ARGUMENTS` 包含 `burn`：额外执行 `python "{{ENGINE}}" --format json`，在水位表下方补充「累计烧入拆解」——主线程 / 子agent / 其他各烧了多少 input、平均每请求多少、燃烧速率。子agent 占比高时提醒用户：子agent 的探索成本也计入本会话总消耗。
