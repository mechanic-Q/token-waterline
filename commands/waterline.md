---
description: 显示当前会话的 Token 水位计（上下文占用/累计烧入/剩余轮数）
argument-hint: [burn]
---
运行 Token 水位计并向用户展示结果。

1. 用 Bash 工具执行：`python "$(cat "$HOME/.zcode/token-waterline.path")" --format gauge`
   （该文件记录引擎的绝对路径，由 hook 每次运行时自动维护。）
   若文件不存在，说明 hook 从未运行过：直接跳到第 4 步告知用户重开会话。
2. 把命令输出**原样**转述给用户（保留水位条与数字，不要自行改写或估算）。
3. 若输出含 ⚠ 或 🚨 建议行，向用户明确强调该建议。
4. 若参数 `$ARGUMENTS` 包含 `burn`：额外执行 `--format json`，在水位表下方补充「累计烧入拆解」——主线程 / 子agent / 其他各烧了多少 input、平均每请求多少、燃烧速率。子agent 占比高时提醒用户：子agent 的探索成本也计入本会话总消耗。
