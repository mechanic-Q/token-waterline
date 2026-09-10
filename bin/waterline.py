#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
token-waterline —— ZCode 会话 Token 水位计

回答三个问题：
  1. 这个会话已经烧了多少 token？（累计 input，含 cache 重发与子 agent，即"账单口径"）
  2. 当前上下文水位多高？（最近一次主线程请求 input / 模型 context 上限）
  3. 按当前增速还剩几轮？（剩余额度 / 近 10 个主线程轮次的平均增量）

数据源（全部只读）：
  - ~/.zcode/cli/db/db.sqlite      model_usage / session 表
  - ~/.zcode/v2/config.json        provider.*.models.*.limit.context
  - ~/.zcode/token-waterline.json  阈值与注入配置（可选，缺省用内置默认值）

用法：
  python waterline.py                          # 人读水位表 (gauge)
  python waterline.py --format line --hook     # hook 用：单行；加 --hook 时输出 additionalContext JSON
  python waterline.py --format json            # 机器可读
  python waterline.py --session sess_xxx       # 指定会话（默认自动定位）
  python waterline.py --list                   # 列出最近会话（辅助定位）
"""

import argparse
import json
import os
import sqlite3
import sys
import time

# ---------------------------------------------------------------- 基础设施

HOME = os.path.expanduser("~")
DEFAULT_DB = os.path.join(HOME, ".zcode", "cli", "db", "db.sqlite")
DEFAULT_ZCODE_CONFIG = os.path.join(HOME, ".zcode", "v2", "config.json")
DEFAULT_THRESHOLD_FILE = os.path.join(HOME, ".zcode", "token-waterline.json")
DEBUG_DIR = os.path.join(HOME, ".zcode", "token-waterline-debug")
# 记录引擎自身的绝对路径，供 /waterline 斜杠命令定位（命令正文不支持插件变量替换）
ENGINE_PATH_FILE = os.path.join(HOME, ".zcode", "token-waterline.path")

DEFAULTS = {
    "warn": 70,                 # 提醒阈值（%）
    "alert": 85,                # 警告阈值
    "critical": 95,             # 危险阈值
    "inject": "always",         # always=每轮注入一行 | threshold=仅超阈值注入 | off=不注入
    "default_context_limit": 1000000,
    "model_limits": {},         # 如 {"glm-5-turbo": 200000}，键小写，优先于 config.json
    "debug": False,             # true 时在 DEBUG_DIR 保留带时间戳的调试记录
}

__version__ = "0.1.0"

BAR_WIDTH = 20


def setup_streams():
    """Windows 控制台/管道默认 gbk，强制 utf-8 避免图标字符炸掉。"""
    for stream in (sys.stdout, sys.stderr, sys.stdin):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def load_thresholds(path):
    cfg = dict(DEFAULTS)
    try:
        with open(path, encoding="utf-8") as f:
            user = json.load(f)
        if isinstance(user, dict):
            cfg.update({k: user[k] for k in DEFAULTS if k in user})
    except FileNotFoundError:
        pass
    except Exception as e:
        print(f"[token-waterline] 阈值配置 {path} 解析失败，用默认值：{e}", file=sys.stderr)
    return cfg


def load_model_limits(zcode_config_path):
    """从 v2/config.json 提取 {(provider_lower, model_lower): ctx} 和 {model_lower: ctx}。"""
    exact, by_model = {}, {}
    try:
        with open(zcode_config_path, encoding="utf-8") as f:
            cfg = json.load(f)
        for prov_id, prov in (cfg.get("provider") or {}).items():
            for model_name, model in (prov.get("models") or {}).items():
                ctx = ((model or {}).get("limit") or {}).get("context")
                if not ctx:
                    continue
                exact[(prov_id.lower(), model_name.lower())] = int(ctx)
                by_model.setdefault(model_name.lower(), int(ctx))
    except FileNotFoundError:
        pass
    except Exception as e:
        print(f"[token-waterline] {zcode_config_path} 解析失败：{e}", file=sys.stderr)
    return exact, by_model


def open_db_ro(db_path):
    posix = db_path.replace("\\", "/")
    conn = sqlite3.connect(f"file:{posix}?mode=ro", uri=True, timeout=3)
    conn.row_factory = sqlite3.Row
    return conn


# ---------------------------------------------------------------- 会话定位

def find_by_key(obj, keys):
    """在任意嵌套层级里找第一个命中的键值。"""
    if isinstance(obj, dict):
        for k in keys:
            v = obj.get(k)
            if isinstance(v, str) and v:
                return v
        for v in obj.values():
            r = find_by_key(v, keys)
            if r:
                return r
    elif isinstance(obj, list):
        for v in obj:
            r = find_by_key(v, keys)
            if r:
                return r
    return None


def norm_dir(p):
    if not p:
        return None
    p = p.replace("\\", "/").rstrip("/").lower()
    return p or None


def read_stdin_payload():
    """hook 会从 stdin 传 JSON；终端手动运行时跳过避免卡住。"""
    try:
        if sys.stdin.isatty():
            return None, ""
        raw = sys.stdin.read()
    except Exception:
        return None, ""
    if not raw.strip():
        return None, raw
    try:
        return json.loads(raw), raw
    except Exception:
        return None, raw


def resolve_session(conn, args, payload, debug):
    """定位优先级：--session > 环境变量 > stdin payload > cwd 匹配最近会话。"""
    sid = args.session
    source = "--session" if sid else None
    if not sid:
        for env in ("CLAUDE_SESSION_ID", "ZCODE_SESSION_ID"):
            sid = os.environ.get(env)
            if sid:
                source = f"env:{env}"
                break
    if not sid and payload:
        sid = find_by_key(payload, ("session_id", "sessionId"))
        if sid:
            source = "stdin"
    if not sid:
        cwd = args.cwd
        if not cwd:
            for env in ("CLAUDE_PROJECT_DIR", "ZCODE_PROJECT_DIR"):
                cwd = os.environ.get(env)
                if cwd:
                    break
        if not cwd and payload:
            cwd = find_by_key(payload, ("cwd", "project_dir", "projectDir", "directory"))
        nd = norm_dir(cwd) or norm_dir(os.getcwd())
        row = conn.execute(
            "SELECT id FROM session WHERE parent_id IS NULL AND time_archived IS NULL "
            "AND LOWER(REPLACE(directory, '\\', '/')) IN (?, ?) "
            "ORDER BY time_updated DESC LIMIT 1",
            (nd, (nd.rstrip("/") + "/") if nd else nd),
        ).fetchone()
        if row:
            sid, source = row["id"], f"cwd:{cwd}"

    if debug is not None:
        debug["session_source"] = source
        debug["session_id"] = sid
    return sid


# ---------------------------------------------------------------- 指标计算

def fetch_metrics(conn, sid):
    meta = conn.execute(
        "SELECT id, title, directory, time_created, time_updated, time_compacting "
        "FROM session WHERE id = ?", (sid,)
    ).fetchone()
    if meta is None:
        return None

    model = conn.execute(
        "SELECT model_id, provider_id FROM model_usage "
        "WHERE session_id = ? AND status = 'completed' "
        "ORDER BY started_at DESC LIMIT 1", (sid,)
    ).fetchone()

    curve = [(r["input_tokens"], r["started_at"]) for r in conn.execute(
        "SELECT input_tokens, started_at FROM model_usage "
        "WHERE session_id = ? AND query_source = 'main_turn' AND status = 'completed' "
        "ORDER BY started_at", (sid,)
    )]

    splits, total = {}, {"reqs": 0, "in": 0, "out": 0, "cache_read": 0,
                         "cache_write": 0, "reasoning": 0, "t0": None, "t1": None}
    for r in conn.execute(
        "SELECT query_source, COUNT(*) n, SUM(input_tokens) i, SUM(output_tokens) o, "
        "SUM(cache_read_input_tokens) cr, SUM(cache_creation_input_tokens) cw, "
        "SUM(reasoning_tokens) rz, MIN(started_at) t0, MAX(COALESCE(completed_at, started_at)) t1 "
        "FROM model_usage WHERE session_id = ? AND status = 'completed' "
        "GROUP BY query_source", (sid,)
    ):
        key = ("main" if r["query_source"] == "main_turn"
               else "subagent" if r["query_source"] == "subagent" else "other")
        splits.setdefault(key, {"reqs": 0, "in": 0})
        splits[key]["reqs"] += r["n"]
        splits[key]["in"] += r["i"] or 0
        total["reqs"] += r["n"]
        total["in"] += r["i"] or 0
        total["out"] += r["o"] or 0
        total["cache_read"] += r["cr"] or 0
        total["cache_write"] += r["cw"] or 0
        total["reasoning"] += r["rz"] or 0
        total["t0"] = r["t0"] if total["t0"] is None else min(total["t0"], r["t0"])
        total["t1"] = r["t1"] if total["t1"] is None else max(total["t1"], r["t1"])

    return {"meta": meta, "model": model, "curve": curve, "splits": splits, "total": total}


def analyze(metrics, limit, now_ms=None):
    now_ms = now_ms or int(time.time() * 1000)
    curve, total = metrics["curve"], metrics["total"]
    out = {"limit": limit}

    if curve:
        current = curve[-1][0]
        out["current_context"] = current
        out["pct"] = current * 100.0 / limit if limit else 0.0
        out["remaining"] = max(0, limit - current)

        deltas = [b[0] - a[0] for a, b in zip(curve, curve[1:])]
        drops = [(i, d) for i, d in enumerate(deltas) if curve[i][0] > 0 and d < -curve[i][0]]
        out["compactions"] = len(drops)
        out["turns_since_compaction"] = len(deltas) - drops[-1][0] if drops else len(deltas)
        recent_pos = [d for d in deltas[-10:] if d > 0]
        out["avg_growth"] = (sum(recent_pos) / len(recent_pos)) if recent_pos else 0
        out["max_recent_growth"] = max(deltas[-10:]) if deltas[-10:] else 0
        out["turns_left"] = (out["remaining"] / out["avg_growth"]) if out["avg_growth"] > 0 else None

    out["burn_in"] = total["in"]
    out["burn_out"] = total["out"]
    out["reqs"] = total["reqs"]
    out["avg_req"] = total["in"] / total["reqs"] if total["reqs"] else 0
    out["cache_read"] = total["cache_read"]
    out["cache_write"] = total["cache_write"]
    out["reasoning"] = total["reasoning"]
    out["splits"] = metrics["splits"]

    if total["t0"] and total["t1"]:
        dur_min = max(0.0, (total["t1"] - total["t0"]) / 60000.0)
        out["duration_min"] = dur_min
        out["rate_per_min"] = total["in"] / dur_min if dur_min >= 1 else None
    out["stale_min"] = max(0, (now_ms - (total["t1"] or 0)) / 60000.0) if total["t1"] else None
    return out


def advise(pct, cfg):
    if pct is None:
        return ""
    if pct >= cfg["critical"]:
        return f"🚨 水位≥{cfg['critical']}%，即将触顶：立即开新会话（或 /compress），避免被强制压缩"
    if pct >= cfg["alert"]:
        return f"⚠️ 水位≥{cfg['alert']}%：强烈建议现在 /compress 或开新会话，避免紧急压缩丢失细节"
    if pct >= cfg["warn"]:
        return f"⚠ 水位≥{cfg['warn']}%：建议收尾规划，考虑 /compress 或开新会话"
    return ""


# ---------------------------------------------------------------- 展示

def ftok(n):
    if n is None:
        return "—"
    n = int(n)
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}K"
    return str(n)


def fnum(n):
    return f"{n:,}"


def fdur(minutes):
    if minutes is None:
        return "—"
    minutes = int(minutes)
    if minutes >= 60:
        return f"{minutes // 60}h{minutes % 60:02d}m"
    return f"{minutes}m"


def bar(pct, width=BAR_WIDTH):
    filled = max(0, min(width, int(round((pct or 0) / 100 * width))))
    return "█" * filled + "░" * (width - filled)


def render_gauge(sid, metrics, a, cfg):
    meta = metrics["meta"]
    model_id = metrics["model"]["model_id"] if metrics["model"] else "?"
    title = (meta["title"] or "")[:40]
    lines = []
    lines.append(f"⛽ Token 水位计 — {model_id}（上限 {ftok(a['limit'])}）  会话 …{sid[-6:]}「{title}」")

    if not metrics["curve"]:
        lines.append("  尚无主线程请求，水位未知。")
        if a["burn_in"]:
            lines.append(f"  本会话累计烧入 {ftok(a['burn_in'])} input（{a['reqs']} 次请求，均为辅助请求）")
        return "\n".join(lines)

    pct = a["pct"]
    lines.append(
        f"当前上下文  ▕{bar(pct)}▏{pct:.1f}%   {fnum(a['current_context'])} / {fnum(a['limit'])}   "
        f"剩余 {ftok(a['remaining'])}"
    )

    if a["turns_left"] is not None:
        tl = a["turns_left"]
        tl_s = ">500" if tl > 500 else f"≈{int(tl)}"
        lines.append(
            f"增长估计    近10轮均值 +{ftok(a['avg_growth'])}/轮 → 还可增长 {tl_s} 轮"
            + (f"（单轮峰值 +{ftok(a['max_recent_growth'])}）" if a["max_recent_growth"] > 0 else "")
        )
    else:
        lines.append("增长估计    近10轮上下文无正增长，无法外推")

    parts = []
    for key, label in (("main", "主线程"), ("subagent", "子agent"), ("other", "其他")):
        if key in a["splits"]:
            parts.append(f"{label} {ftok(a['splits'][key]['in'])}")
    lines.append(
        f"本会话累计  烧入 {ftok(a['burn_in'])} input｜{a['reqs']} 次请求｜均值 {ftok(a['avg_req'])}/次"
        + (f"｜产出 {ftok(a['burn_out'])}" if a["burn_out"] else "")
    )
    lines.append(f"拆分        {'｜'.join(parts) if parts else '—'}")

    rate = f"｜速率 {ftok(a['rate_per_min'])}/min" if a.get("rate_per_min") else ""
    extra = ""
    if a["compactions"]:
        extra = f"｜含 {a['compactions']} 次压缩（距上次 {a['turns_since_compaction']} 轮）"
    stale = f"（末次活动 {int(a['stale_min'])} 分钟前）" if a.get("stale_min") and a["stale_min"] >= 30 else ""
    lines.append(f"燃烧记录    时长 {fdur(a.get('duration_min'))}{rate}{extra}{stale}")

    tip = advise(pct, cfg)
    if tip:
        lines.append(tip)
    return "\n".join(lines)


def render_line(a, cfg, hook=False):
    """单行摘要。hook 模式下输出 additionalContext JSON；低于注入策略时输出空。"""
    if a["reqs"] == 0 or a.get("pct") is None:
        return "" if hook else "（尚无主线程请求，水位未知）"
    pct = a["pct"]
    base = (f"[Token水位] {pct:.1f}% ({ftok(a['current_context'])}/{ftok(a['limit'])})"
            f"｜本会话已烧 {ftok(a['burn_in'])}")
    if a["turns_left"] is not None:
        tl = a["turns_left"]
        base += f"｜剩余≈{'>500' if tl > 500 else int(tl)}轮"
    tip = advise(pct, cfg)
    text = base + (f"｜{tip}" if tip else "")

    if not hook:
        return text
    mode = cfg.get("inject", "always")
    if mode == "off" or (mode == "threshold" and not tip):
        return ""
    return json.dumps({"additionalContext": f"[token-waterline] {text}"}, ensure_ascii=False)


def render_json(sid, metrics, a, cfg):
    meta = metrics["meta"]
    return json.dumps({
        "session": {"id": sid, "title": meta["title"], "directory": meta["directory"],
                    "compacting": meta["time_compacting"] is not None},
        "model": dict(metrics["model"]) if metrics["model"] else None,
        "limit": a["limit"],
        "current_context": a.get("current_context"),
        "pct": round(a["pct"], 2) if a.get("pct") is not None else None,
        "remaining": a.get("remaining"),
        "avg_growth_per_turn": a.get("avg_growth"),
        "max_recent_growth": a.get("max_recent_growth"),
        "turns_left": a.get("turns_left"),
        "compactions": a.get("compactions"),
        "turns_since_compaction": a.get("turns_since_compaction"),
        "burn": {"input": a["burn_in"], "output": a["burn_out"], "requests": a["reqs"],
                 "avg_input_per_request": round(a["avg_req"]) if a["reqs"] else 0,
                 "cache_read": a["cache_read"], "cache_write": a["cache_write"],
                 "reasoning": a["reasoning"],
                 "splits": {k: v["in"] for k, v in a["splits"].items()},
                 "duration_min": a.get("duration_min"),
                 "rate_per_min": a.get("rate_per_min"),
                 "stale_min": a.get("stale_min")},
        "thresholds": {k: cfg[k] for k in ("warn", "alert", "critical")},
        "advice": advise(a.get("pct"), cfg),
    }, ensure_ascii=False, indent=2)


def render_list(conn):
    rows = conn.execute(
        "SELECT s.id, s.title, s.directory, s.time_updated, "
        "COALESCE((SELECT SUM(m.input_tokens) FROM model_usage m "
        "  WHERE m.session_id = s.id AND m.status = 'completed'), 0) burned "
        "FROM session s WHERE s.parent_id IS NULL AND s.time_archived IS NULL "
        "ORDER BY s.time_updated DESC LIMIT 15"
    ).fetchall()
    out = ["最近会话（按活跃时间）："]
    for r in rows:
        age = int(max(0, time.time() * 1000 - r["time_updated"]) / 60000)
        out.append(f"  …{r['id'][-6:]}  {ftok(r['burned']):>7}  {age:>5}m前  {(r['title'] or '')[:36]}  [{r['directory']}]")
    out.append("用 --session <id> 或在对应项目目录下运行可精确指定。")
    return "\n".join(out)


# ---------------------------------------------------------------- 入口

def write_debug(payload_raw, extra):
    try:
        os.makedirs(DEBUG_DIR, exist_ok=True)
        with open(os.path.join(DEBUG_DIR, "last-stdin.json"), "w", encoding="utf-8") as f:
            f.write(payload_raw or "(empty)")
        with open(os.path.join(DEBUG_DIR, "last-resolved.json"), "w", encoding="utf-8") as f:
            json.dump(extra, f, ensure_ascii=False, indent=2, default=str)
        if extra.get("keep_history"):
            ts = time.strftime("%Y%m%d-%H%M%S")
            with open(os.path.join(DEBUG_DIR, f"stdin-{ts}.json"), "w", encoding="utf-8") as f:
                f.write(payload_raw or "(empty)")
    except Exception:
        pass


def remember_engine_path():
    """把自己的绝对路径写到固定位置，供 /waterline 命令读取（命令正文无插件变量替换）。"""
    try:
        me = os.path.abspath(__file__)
        try:
            with open(ENGINE_PATH_FILE, encoding="utf-8") as f:
                if f.read().strip() == me:
                    return
        except OSError:
            pass
        os.makedirs(os.path.dirname(ENGINE_PATH_FILE), exist_ok=True)
        with open(ENGINE_PATH_FILE, "w", encoding="utf-8") as f:
            f.write(me)
    except Exception:
        pass


def main():
    setup_streams()
    remember_engine_path()
    ap = argparse.ArgumentParser(description="ZCode 会话 Token 水位计")
    ap.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    ap.add_argument("--format", choices=["gauge", "line", "json"], default="gauge")
    ap.add_argument("--hook", action="store_true", help="配合 --format line：输出 hook JSON")
    ap.add_argument("--session", help="指定 session id（默认自动定位）")
    ap.add_argument("--cwd", help="覆盖用于定位会话的工作目录")
    ap.add_argument("--db", default=DEFAULT_DB)
    ap.add_argument("--config", default=DEFAULT_THRESHOLD_FILE, help="阈值配置 json")
    ap.add_argument("--zcode-config", default=DEFAULT_ZCODE_CONFIG)
    ap.add_argument("--list", action="store_true", help="列出最近会话")
    args = ap.parse_args()

    cfg = load_thresholds(args.config)
    payload, payload_raw = read_stdin_payload()

    debug = {"keep_history": bool(cfg.get("debug"))}
    try:
        conn = open_db_ro(args.db)
    except Exception as e:
        write_debug(payload_raw, {**debug, "error": f"db open: {e}"})
        if args.format == "line":
            return 0  # hook 场景静默退出，绝不打扰会话
        print(f"无法打开数据库 {args.db}: {e}", file=sys.stderr)
        return 1

    if args.list:
        print(render_list(conn))
        return 0

    sid = resolve_session(conn, args, payload, debug)
    out = ""
    exit_code = 0
    if not sid:
        debug["error"] = "no session resolved"
        out = "" if args.format == "line" else "未找到会话（当前目录没有活跃的 ZCode 会话）"
    else:
        try:
            metrics = fetch_metrics(conn, sid)
            if metrics is None:
                debug["error"] = f"session {sid} not found"
                out = "" if args.format == "line" else f"会话 {sid} 不存在"
            else:
                exact, by_model = load_model_limits(args.zcode_config)
                model_id = metrics["model"]["model_id"] if metrics["model"] else ""
                provider_id = metrics["model"]["provider_id"] if metrics["model"] else ""
                limit = (cfg["model_limits"].get(model_id.lower())
                         or exact.get((provider_id.lower(), model_id.lower()))
                         or by_model.get(model_id.lower())
                         or cfg["default_context_limit"])
                a = analyze(metrics, limit)
                debug.update({"model": model_id, "limit": limit, "pct": a.get("pct")})
                if args.format == "json":
                    out = render_json(sid, metrics, a, cfg)
                elif args.format == "line":
                    out = render_line(a, cfg, hook=args.hook)
                else:
                    out = render_gauge(sid, metrics, a, cfg)
        except Exception as e:
            debug["error"] = repr(e)
            if args.format == "line":
                out = ""          # hook 场景静默
                exit_code = 0
            else:
                out = f"[token-waterline] 计算出错：{e!r}"
                exit_code = 1

    if cfg.get("debug") or os.environ.get("WATERLINE_DEBUG"):
        write_debug(payload_raw, debug)
    elif args.format == "line" and payload_raw.strip():
        write_debug(payload_raw, debug)  # 探针阶段：留痕最近一次 hook 输入，便于验证联通

    if out:
        print(out)
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
