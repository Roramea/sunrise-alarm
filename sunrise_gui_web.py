#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""日出光闹钟 · 网页版 GUI（pywebview 桌面壳）

本文件只是「壳」：
- 用 pywebview 把一个本地网页（webui/）渲染成原生窗口
- 所有背光 / DDC / 点亮 / 爬升逻辑，一律通过子进程调用 sunrise_alarm.py 的 CLI
- 不重写、也不 import 任何 DDC / 背光核心逻辑

用法：
    python sunrise_gui_web.py            # 正常打开窗口
    python sunrise_gui_web.py --smoke 8  # 自检：开窗后 8 秒自动关闭（验收用）
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
import traceback

BASE = os.path.dirname(os.path.abspath(__file__))
WEBUI_DIR = os.path.join(BASE, "webui")
CONFIG_PATH = os.path.join(BASE, "config.json")
STATE_PATH = os.path.join(BASE, "state.json")
ALARM_PY = os.path.join(BASE, "sunrise_alarm.py")
TASK_NAME = "SunriseAlarm"
LOG_PATH = os.path.join(BASE, "sunrise_webui.log")
CRASH_PATH = os.path.join(BASE, "sunrise_webui_crash.log")

# 与 sunrise_alarm.py 的 DEFAULT_CONFIG 对齐（这里只用于「缺失键补默认」，不参与背光逻辑）
DEFAULT_CONFIG = {
    "start_time": "07:00",
    "end_time": "07:30",
    "start_level": 5,
    "max_level": 100,
    "curve": 1.0,
    "curve_points": None,
    "holidays": [],
    "holiday_skip": True,
    "tick_seconds": 5,
    "overlay": True,
    "overlay_mode": "sunrise",
    "hold": True,
    "hold_max_minutes": 0,
    "overlay_start_color": [255, 112, 24],
    "overlay_end_color": [255, 255, 255],
    "wake_timeout": 25,
    "monitor_index": 0,
    "task_time": "07:00",
    "task_weekdays": ["MON", "TUE", "WED", "THU", "FRI"],
}

OVERLAY_MODES = ("off", "white", "sunrise", "sky")
WEEKDAYS = [("MON", "周一"), ("TUE", "周二"), ("WED", "周三"), ("THU", "周四"),
            ("FRI", "周五"), ("SAT", "周六"), ("SUN", "周日")]


def log(msg: str) -> None:
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write("%s %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg))
    except OSError:
        pass


# --------------------------------------------------------------------- 进程/CLI

def _pythonw() -> str:
    exe = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    return exe if os.path.exists(exe) else sys.executable


def _parse_hhmm(s: str):
    parts = str(s).strip().split(":")
    if len(parts) != 2:
        raise ValueError("时间格式要 HH:MM（例 07:00）")
    h, m = int(parts[0]), int(parts[1])
    if not (0 <= h <= 23 and 0 <= m <= 59):
        raise ValueError("时间超出范围（00:00 ~ 23:59）")
    return h, m


def run_cli(extra, timeout=20):
    """同步跑一次 CLI，返回 (rc, stdout+stderr)。用于 --get / --status / 任务查询等。"""
    cmd = [sys.executable, ALARM_PY, *extra]
    try:
        r = subprocess.run(cmd, cwd=BASE, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=timeout)
        out = (r.stdout or "") + (r.returncode and (r.stderr or "") or "")
        return r.returncode, (out or "").strip()
    except subprocess.TimeoutExpired:
        return -1, "命令超时（%ss）：%s" % (timeout, " ".join(extra))
    except OSError as e:
        return -1, "启动失败：%s" % e


def spawn_cli(extra):
    """分离式起一个 CLI 子进程（点亮/爬升/预览/停止），与 tkinter 版做法一致。"""
    cmd = [_pythonw(), ALARM_PY, *extra]
    flags = 0x00000008 if os.name == "nt" else 0        # DETACHED_PROCESS
    subprocess.Popen(cmd, cwd=BASE, creationflags=flags,
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL)


# --------------------------------------------------------------------- config 读写

def load_config() -> dict:
    cfg = dict(DEFAULT_CONFIG)
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, encoding="utf-8") as f:
                cfg.update(json.load(f))
        except (OSError, ValueError) as e:
            log("config.json 读取失败，用默认值: %s" % e)
    return cfg


def save_config(cfg: dict) -> None:
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


def read_state():
    if not os.path.exists(STATE_PATH):
        return None
    try:
        with open(STATE_PATH, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def task_query() -> str:
    try:
        r = subprocess.run(["schtasks", "/Query", "/TN", TASK_NAME, "/V", "/FO", "LIST"],
                           capture_output=True, text=True, encoding="gbk", errors="replace")
        return (((r.stdout or "") + (r.stderr or "")).strip()) or "（无输出）"
    except OSError as e:
        return "查询失败：%s" % e


# --------------------------------------------------------------------- JS-API

class Api:
    """JS-API。注意：本进程是 pythonnet 承载的 GUI 进程，
    不要把「常驻后台线程 + 反复 spawn 子进程」和「页面并发调 js_api」叠在一起——
    实测那种组合会把 python 线程饿死（窗口假死）。改成页面【按需】调用，
    每次调用内部同步跑一次 CLI（实测这种串行方式稳定、UI 不卡）。"""

    def __init__(self):
        # 注意：窗口引用必须存成【私有】属性（_win）。
        # pywebview 会遍历 js_api 对象的公开成员来搭桥，公开的 window 属性
        # 指向 pywebview 的 Window 对象 → 会把线程饿死（实测窗口假死）。
        self._win = None
        self._lock = threading.Lock()

    # -- 窗口 --------------------------------------------------------------
    def set_window(self, w):
        self._win = w

    def close(self):           # 供页面「关闭窗口」按钮调用
        if self._win is not None:
            self._win.destroy()

    # -- config ------------------------------------------------------------
    def load_config(self):
        return load_config()

    def save_config(self, data):
        """把界面数据合并进 config.json（保留未知键），并做基本校验。"""
        try:
            cfg = load_config()
            cfg["start_time"] = str(data["start_time"]).strip()
            cfg["end_time"] = str(data["end_time"]).strip()
            cfg["task_time"] = str(data.get("task_time") or data["start_time"]).strip()
            cfg["start_level"] = int(float(data["start_level"]))
            cfg["max_level"] = int(float(data["max_level"]))
            cfg["curve"] = float(data["curve"])
            cfg["tick_seconds"] = float(data.get("tick_seconds") or 5)
            cfg["overlay"] = bool(data.get("overlay", True))
            mode = str(data.get("overlay_mode", "sunrise")).strip().lower()
            cfg["overlay_mode"] = mode if mode in OVERLAY_MODES else "sunrise"
            cfg["hold"] = bool(data.get("hold", True))
            cfg["hold_max_minutes"] = float(data.get("hold_max_minutes") or 0)
            cfg["monitor_index"] = int(data.get("monitor_index") or 0)
            days = data.get("task_weekdays") or []
            cfg["task_weekdays"] = [d for d in days if d in dict(WEEKDAYS)]
            # curve_points：空串/空数组 → None
            cp = data.get("curve_points", cfg.get("curve_points"))
            if cp in ("", None, [], "null", "None"):
                cfg["curve_points"] = None
            elif isinstance(cp, (list, tuple)):
                cfg["curve_points"] = [list(p) for p in cp]
            else:
                cfg["curve_points"] = json.loads(cp)
            # 校验时间格式
            _parse_hhmm(cfg["start_time"])
            _parse_hhmm(cfg["end_time"])
            _parse_hhmm(cfg["task_time"])
            save_config(cfg)
            log("config 已保存: %s" % json.dumps(
                {k: cfg[k] for k in ("start_time", "end_time", "start_level", "max_level")},
                ensure_ascii=False))
            return {"ok": True, "config": cfg}
        except (KeyError, ValueError, TypeError) as e:
            return {"ok": False, "error": str(e)}
        except json.JSONDecodeError as e:
            return {"ok": False, "error": "curve_points 不是合法 JSON：%s" % e}

    # -- 实时（页面按需调用，内部同步跑一次 CLI） ---------------------------
    def snapshot(self):
        """返回 {brightness, bright_err, state}。前端每几秒调一次。"""
        with self._lock:
            rc, out = run_cli(["--get"], timeout=12)
            b = None
            err = None
            if rc == 0 and out:
                last = out.strip().splitlines()[-1].strip()
                if last.lstrip("-").isdigit():
                    b = int(last)
                elif last.lower() == "none":
                    err = "读不到亮度（屏幕可能在省电态）"
                else:
                    err = out[-200:]
            else:
                err = out[-200:] if out else "读取失败"
            return {"brightness": b, "bright_err": err, "state": read_state(),
                    "ts": time.time()}

    def refresh(self):
        return self.snapshot()

    # -- 动作 --------------------------------------------------------------
    def preview(self, seconds=10, overlay=True, hold=False):
        try:
            secs = max(1, int(float(seconds)))
        except (TypeError, ValueError):
            return {"ok": False, "error": "预览秒数不对"}
        extra = ["--preview", str(secs)]
        if not overlay:
            extra.append("--no-overlay")
        extra.append("--hold" if hold else "--no-hold")
        try:
            spawn_cli(extra)
            return {"ok": True, "cmd": " ".join(extra)}
        except OSError as e:
            return {"ok": False, "error": str(e)}

    def start(self, overlay=True, hold=True):
        extra = ["--run"]
        if not overlay:
            extra.append("--no-overlay")
        extra.append("--hold" if hold else "--no-hold")
        try:
            spawn_cli(extra)
            return {"ok": True, "cmd": " ".join(extra)}
        except OSError as e:
            return {"ok": False, "error": str(e)}

    def stop(self):
        rc, out = run_cli(["--stop"], timeout=15)
        return {"ok": rc == 0, "output": out}

    def wake(self):
        rc, out = run_cli(["--wake"], timeout=40)
        return {"ok": rc == 0, "output": out}

    def skip_next(self):
        rc, out = run_cli(["--skip-next"], timeout=15)
        return {"ok": rc == 0, "output": out}

    def list_monitors(self):
        rc, out = run_cli(["--list"], timeout=20)
        return {"ok": rc == 0, "output": out}

    # -- 计划任务（只按钮触发，不自动改） -----------------------------------
    def install_task(self):
        rc, out = run_cli(["--install-task"], timeout=60)
        return {"ok": rc == 0, "output": out}

    def remove_task(self):
        rc, out = run_cli(["--remove-task"], timeout=60)
        return {"ok": rc == 0, "output": out}

    def task_query(self):
        return {"output": task_query()}


# --------------------------------------------------------------------- 入口

def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    smoke = 0
    if "--smoke" in argv:
        i = argv.index("--smoke")
        try:
            smoke = int(argv[i + 1]) if i + 1 < len(argv) else 6
        except ValueError:
            smoke = 6

    import webview

    api = Api()
    index = os.path.join(WEBUI_DIR, "index.html")
    window = webview.create_window(
        "日出光闹钟 · 网页版", index, js_api=api,
        width=980, height=760, min_size=(860, 620), background_color="#0b1220")
    api.set_window(window)

    def after_start():
        log("窗口已启动%s" % ("（smoke %ss）" % smoke if smoke else ""))
        if smoke:
            time.sleep(smoke)
            log("smoke 到点，关闭窗口")
            try:
                window.destroy()
                log("destroy 已调用")
            except Exception:                       # noqa: BLE001
                log("destroy 失败: %s" % traceback.format_exc())

    log("启动 webui GUI")
    webview.start(after_start)
    log("窗口已关闭，退出")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except BaseException:                           # 兜底：pythonw 下 stderr 会被吞
        with open(CRASH_PATH, "a", encoding="utf-8") as f:
            f.write("\n=== %s ===\n%s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"),
                                            traceback.format_exc()))
        log("入口未捕获异常，见 %s" % CRASH_PATH)
        sys.exit(1)
