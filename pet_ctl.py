"""宠物状态桥 CLI。

用法：
    python pet_ctl.py set <state> [msg] [--step "<子步骤>"] [--progress <cur>/<total>]
    python pet_ctl.py get
    python pet_ctl.py reset
    python pet_ctl.py prefs [key] [true|false]    # 列出 / 设置 / 恢复

state 取值：idle / thinking / running / waiting / done / failed / sleeping

进度会被写进 status.json 的 progress 字段，由 pet.py 在气泡下方画一条进度条 + 文字。
prefs 控制宠物底带渲染：show_step_text / show_progress_bar / show_count / compact。
"""
from __future__ import annotations

import json
import os
import sys
import time

STATUS_JSON = os.path.join(os.path.expanduser("~"), ".workbuddy", "pet", "status.json")
PREFS_JSON = os.path.join(os.path.expanduser("~"), ".workbuddy", "pet", "prefs.json")
STATES = ("idle", "thinking", "running", "waiting", "done", "failed", "sleeping")

DEFAULT_PREFS = {
    "show_strip": True,            # 总开关：false 时整个底带都不画（最省事）
    "show_step_text": True,        # 步骤文字
    "show_progress_bar": True,     # 4px 进度条
    "show_count": True,            # 右下角 i/n 计数
    "compact": False,              # 紧凑模式：自动关掉步骤文字
    "sound": True,                 # 音效总开关
}

PREFS_KEYS = tuple(DEFAULT_PREFS.keys())

_TRUTHY = {"true", "1", "yes", "on"}
_FALSY = {"false", "0", "no", "off"}


def _parse_bool(v: str) -> bool:
    lv = v.lower()
    if lv in _TRUTHY:
        return True
    if lv in _FALSY:
        return False
    raise ValueError(f"无法解析布尔值: {v!r}（应为 true/false/1/0/yes/no/on/off）")


def _ensure() -> None:
    os.makedirs(os.path.dirname(STATUS_JSON), exist_ok=True)


def _parse_progress(spec: str) -> dict | None:
    """解析 "2/7" 或 "2/7/29"（cur/total[/percent]）。

    规则：cur/total 都必须为正整数，否则视为无效；percent 可省略，自动算。
    """
    if not spec:
        return None
    parts = spec.split("/")
    if len(parts) not in (2, 3):
        raise ValueError(f"--progress 应为 cur/total 或 cur/total/percent，得到 {spec!r}")
    try:
        cur = int(parts[0])
        total = int(parts[1])
        pct = int(parts[2]) if len(parts) == 3 else None
    except ValueError as e:
        raise ValueError(f"--progress 数字解析失败: {spec!r}") from e
    if cur < 0 or total <= 0 or cur > total:
        raise ValueError(f"--progress 越界 cur={cur} total={total}")
    if pct is None:
        pct = round(cur * 100 / total)
    return {"current": cur, "total": total, "percent": pct}


def cmd_set(state: str, msg: str, step: str = "", progress: str = "") -> None:
    if state not in STATES:
        print(f"unknown state: {state}（应为 {STATES}）", file=sys.stderr)
        sys.exit(2)

    prog: dict | None = None
    if progress:
        try:
            prog = _parse_progress(progress)
        except ValueError as e:
            print(str(e), file=sys.stderr)
            sys.exit(2)

    _ensure()
    payload = {
        "state": state,
        "msg": msg,
        "step": step,
        "progress": prog,
        "updated": time.time(),
    }
    tmp = STATUS_JSON + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False)
    os.replace(tmp, STATUS_JSON)

    pieces = [f"state={state}", f"msg={msg!r}"]
    if step:
        pieces.append(f"step={step!r}")
    if prog:
        pieces.append(f"progress={prog['current']}/{prog['total']} ({prog['percent']}%)")
    print(f"[pet] {' '.join(pieces)}")


def cmd_get() -> None:
    if os.path.exists(STATUS_JSON):
        print(open(STATUS_JSON, encoding="utf-8").read(), end="")
    else:
        print("{}")


def cmd_reset() -> None:
    cmd_set("idle", "", "", "")


# ---------- prefs ----------
def _load_prefs() -> dict:
    if not os.path.exists(PREFS_JSON):
        return dict(DEFAULT_PREFS)
    try:
        with open(PREFS_JSON, "r", encoding="utf-8") as f:
            d = json.load(f)
    except Exception:
        return dict(DEFAULT_PREFS)
    # 缺失的 key 用默认值补
    out = dict(DEFAULT_PREFS)
    for k in PREFS_KEYS:
        if k in d and isinstance(d[k], bool):
            out[k] = d[k]
    return out


def _save_prefs(prefs: dict) -> None:
    _ensure()
    tmp = PREFS_JSON + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(prefs, f, ensure_ascii=False, indent=2)
    os.replace(tmp, PREFS_JSON)


def cmd_prefs(key: str | None = None, value: str | None = None) -> None:
    if key in (None, ""):
        # 列出所有
        prefs = _load_prefs()
        for k in PREFS_KEYS:
            print(f"  {k} = {prefs[k]}")
        return
    if key == "reset":
        _save_prefs(dict(DEFAULT_PREFS))
        print("[pet] prefs reset to defaults")
        return
    if key not in PREFS_KEYS:
        print(f"未知 pref: {key!r}（应为 {PREFS_KEYS}）", file=sys.stderr)
        sys.exit(2)
    prefs = _load_prefs()
    if value is None:
        # 单项取值
        print(f"  {key} = {prefs[key]}")
        return
    prefs[key] = _parse_bool(value)
    _save_prefs(prefs)
    print(f"[pet] prefs {key} = {prefs[key]}")


def main() -> None:
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(0)
    cmd = sys.argv[1]
    if cmd == "set":
        _dispatch_set(sys.argv[2:])
    elif cmd == "get":
        cmd_get()
    elif cmd in ("reset", "clear"):
        cmd_reset()
    elif cmd == "prefs":
        key = sys.argv[2] if len(sys.argv) > 2 else None
        value = sys.argv[3] if len(sys.argv) > 3 else None
        try:
            cmd_prefs(key, value)
        except ValueError as e:
            print(str(e), file=sys.stderr)
            sys.exit(2)
    else:
        print(__doc__)
        sys.exit(2)


def _dispatch_set(args: list[str]) -> None:
    """set <state> [msg] [--step X] [--progress Y]"""
    if not args:
        print("set 需要 state 参数", file=sys.stderr)
        sys.exit(2)
    state = args[0]
    rest = args[1:]

    msg = ""
    step = ""
    progress = ""
    msg_tokens: list[str] = []
    i = 0
    while i < len(rest):
        a = rest[i]
        if a in ("--step", "-s"):
            if i + 1 >= len(rest):
                print("--step 需要参数", file=sys.stderr)
                sys.exit(2)
            step = rest[i + 1]
            i += 2
        elif a in ("--progress", "-p"):
            if i + 1 >= len(rest):
                print("--progress 需要参数", file=sys.stderr)
                sys.exit(2)
            progress = rest[i + 1]
            i += 2
        elif a.startswith("--"):
            print(f"未知选项 {a}", file=sys.stderr)
            sys.exit(2)
        else:
            msg_tokens.append(a)
            i += 1
    msg = " ".join(msg_tokens)

    cmd_set(state, msg, step, progress)


if __name__ == "__main__":
    main()