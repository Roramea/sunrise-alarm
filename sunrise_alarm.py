#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""日出光闹钟 (Sunrise Alarm) — 用显示器背光 + 全屏日光层做缓亮光闹钟。

三段式：
  1. 强制点亮：先把显示器从省电/待机里拉回来（广播 SC_MONITORPOWER、模拟输入、
     DDC/CI 电源指令 0xD6=1），并轮询确认 DDC 真的能读到了才开始——屏幕没亮，
     调亮度是没用的。
  2. 背光爬升：DDC/CI（Windows 自带 dxva2.dll）把背光从低慢慢爬到最高。
  3. 日光层：爬升期间铺一层全屏窗口，颜色由暖橙渐变到白，既是光源也是进度显示。
     随时按 ESC / 点一下 / 全局热键 Ctrl+Alt+Q 强制退出，不会卡住。

不依赖任何第三方软件，也不需要 Twinkle Tray。

用法:
  双击 启动GUI.bat                          # 图形界面（推荐）
  python sunrise_alarm.py --run             # 按 config.json 跑一次完整流程（计划任务调用）
  python sunrise_alarm.py --preview 15      # 把整段压缩到 15 秒，含日光层，可 ESC 退出
  python sunrise_alarm.py --status          # 当前亮度 / 是否在跑 / 下次计划
  python sunrise_alarm.py --stop            # 停止正在跑的爬升
  python sunrise_alarm.py --list            # 列出显示器
  python sunrise_alarm.py --wake            # 只做"强制点亮屏幕"并报告结果
  python sunrise_alarm.py --install-task    # 注册/更新计划任务（需管理员）
  python sunrise_alarm.py --remove-task
"""
from __future__ import annotations

import argparse
import ctypes
import datetime as dt
import faulthandler
import json
import os
import subprocess
import sys
import threading
import time
import traceback
from ctypes import wintypes

if sys.platform != "win32":
    raise SystemExit("这个工具依赖 Windows 的 DDC/CI 接口，只能在 Windows 上跑。")

BASE = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE, "config.json")
LOG_PATH = os.path.join(BASE, "sunrise_alarm.log")
CRASH_PATH = os.path.join(BASE, "crash.log")
STATE_PATH = os.path.join(BASE, "state.json")
STOP_PATH = os.path.join(BASE, "stop.flag")
TASK_NAME = "SunriseAlarm"

WEEKDAYS = [("MON", "周一"), ("TUE", "周二"), ("WED", "周三"), ("THU", "周四"),
            ("FRI", "周五"), ("SAT", "周六"), ("SUN", "周日")]

DEFAULT_CONFIG = {
    "start_time": "07:00",          # 开始爬升
    "end_time": "07:30",            # 到达最高亮度
    "start_level": 5,               # 起始亮度（0 会全黑，建议 3~10）
    "max_level": 100,               # 最高亮度
    "curve": 1.0,                   # 1=线性；>1 前段更慢
    "tick_seconds": 5,              # 每隔几秒检查并写一次亮度
    "overlay": True,                # 爬升期间铺全屏日光层
    "hold": True,                   # 到最高亮度后保持，直到手动关闭（像闹钟最后一响）
    "hold_max_minutes": 0,          # 保持上限（分钟）；0 = 不限，一直保持到手动关闭
    "overlay_start_color": [255, 112, 24],    # 日光层起始色（暖橙）
    "overlay_end_color": [255, 255, 255],     # 日光层结束色（白）
    "wake_timeout": 25,             # 强制点亮最多等多少秒
    "monitor_index": 0,             # 第几台显示器（0 起）
    "task_time": "07:00",
    "task_weekdays": ["MON", "TUE", "WED", "THU", "FRI"],
}

user32 = ctypes.WinDLL("user32", use_last_error=True)
dxva2 = ctypes.WinDLL("dxva2", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

WM_SYSCOMMAND = 0x0112
SC_MONITORPOWER = 0xF170
HWND_BROADCAST = 0xFFFF
MOUSEEVENTF_MOVE = 0x0001
ES_CONTINUOUS = 0x80000000
ES_SYSTEM_REQUIRED = 0x00000001
ES_DISPLAY_REQUIRED = 0x00000002

WM_HOTKEY = 0x0312
WM_QUIT = 0x0012
MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
VK_Q = 0x51

VCP_POWER_MODE = 0xD6          # DDC/CI: 1=开, 2=待机, 3=挂起, 4=硬关, 5=软关

try:
    _set_vcp = dxva2.SetVCPFeature
    _set_vcp.restype = wintypes.BOOL
except AttributeError:                                          # pragma: no cover
    _set_vcp = None


class PHYSICAL_MONITOR(ctypes.Structure):
    _fields_ = [("hPhysicalMonitor", wintypes.HANDLE),
                ("szPhysicalMonitorDescription", wintypes.WCHAR * 128)]


MONITORENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HMONITOR,
                                     wintypes.HDC, ctypes.POINTER(wintypes.RECT),
                                     wintypes.LPARAM)


def log(msg: str) -> None:
    line = f"[{dt.datetime.now():%Y-%m-%d %H:%M:%S}] {msg}"
    print(line, flush=True)
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


# --------------------------------------------------------------------------- 崩溃捕获
# pythonw + 计划任务下没有 stderr：未捕获异常会静默退 1，traceback 无处可去。
# 这里把 faulthandler（硬崩溃）+ 主线程/子线程/Tk 回调 的异常全部落到 crash.log。
_CRASH_FILE = None


def log_traceback(where: str, exc_type=None, exc=None, tb=None) -> str:
    """把一段 traceback 追加到 crash.log（同时尝试打印）。"""
    if exc is None:
        text = traceback.format_exc()
    else:
        text = "".join(traceback.format_exception(exc_type, exc, tb))
    block = (f"\n===== {dt.datetime.now():%Y-%m-%d %H:%M:%S} 异常 @ {where} "
             f"(pid={os.getpid()}, {threading.current_thread().name}) =====\n{text}")
    try:
        with open(CRASH_PATH, "a", encoding="utf-8") as f:
            f.write(block)
    except OSError:
        pass
    try:
        print(block, flush=True)
    except Exception:
        pass
    return text


def install_crash_capture(path: str | None = None) -> None:
    """装上崩溃捕获；返回前 crash.log 已可写。"""
    global _CRASH_FILE
    try:
        _CRASH_FILE = open(path or CRASH_PATH, "a", encoding="utf-8", buffering=1)
    except OSError:
        _CRASH_FILE = None
    if _CRASH_FILE is not None:
        try:
            faulthandler.enable(_CRASH_FILE, all_threads=True)
        except Exception:                                          # noqa: BLE001
            pass

    def _main_hook(exc_type, exc, tb):
        log_traceback("主线程", exc_type, exc, tb)
        log(f"未捕获异常: {exc_type.__name__}: {exc}")

    sys.excepthook = _main_hook

    if hasattr(threading, "excepthook"):
        def _thread_hook(args):
            name = args.thread.name if args.thread is not None else "?"
            log_traceback(f"线程 {name}", args.exc_type, args.exc_value, args.exc_traceback)
            log(f"线程异常: {args.exc_type.__name__}: {args.exc_value}")

        threading.excepthook = _thread_hook


def enumerate_hmonitors():
    found = []
    user32.EnumDisplayMonitors(
        None, None, MONITORENUMPROC(lambda h, d, r, x: (found.append(h), True)[1]), 0)
    return found


class DDCMonitor:
    """通过 DDC/CI 读写显示器的背光亮度（第一台或指定的一台）。"""

    def __init__(self, index: int = 0):
        self.index = index
        self._arrays = []
        self._handles = []
        self.handle = None
        self.desc = ""
        self.minimum, self.maximum = 0, 100

    def open(self) -> "DDCMonitor":
        self.close()
        for hmon in enumerate_hmonitors():
            n = wintypes.DWORD(0)
            if not dxva2.GetNumberOfPhysicalMonitorsFromHMONITOR(hmon, ctypes.byref(n)):
                continue
            if n.value == 0:
                continue
            arr = (PHYSICAL_MONITOR * n.value)()
            if not dxva2.GetPhysicalMonitorsFromHMONITOR(hmon, n.value, arr):
                continue
            self._arrays.append(arr)
            self._handles.extend(arr[j] for j in range(n.value))
        if not self._handles:
            raise RuntimeError("找不到可用的物理显示器（DDC/CI 可能被关闭）")
        idx = max(0, min(int(self.index), len(self._handles) - 1))
        pm = self._handles[idx]
        self.handle = pm.hPhysicalMonitor
        self.desc = pm.szPhysicalMonitorDescription or f"显示器 {idx}"
        mn, cur, mx = wintypes.DWORD(0), wintypes.DWORD(0), wintypes.DWORD(0)
        if dxva2.GetMonitorBrightness(self.handle, ctypes.byref(mn),
                                      ctypes.byref(cur), ctypes.byref(mx)):
            self.minimum, self.maximum = mn.value, mx.value
        return self

    def get_brightness(self):
        cur, mn, mx = (wintypes.DWORD(0) for _ in range(3))
        if not dxva2.GetMonitorBrightness(self.handle, ctypes.byref(mn),
                                          ctypes.byref(cur), ctypes.byref(mx)):
            return None
        self.minimum, self.maximum = mn.value, mx.value
        return cur.value

    def set_brightness(self, value: int) -> bool:
        v = max(self.minimum, min(self.maximum, int(value)))
        return bool(dxva2.SetMonitorBrightness(self.handle, v))

    def power_on(self) -> bool:
        """DDC/CI 电源指令，把显示器从待机/软关状态打开（不是所有型号都支持）。"""
        if _set_vcp is None:
            return False
        try:
            return bool(_set_vcp(self.handle, VCP_POWER_MODE, 1))
        except Exception:
            return False

    def close(self) -> None:
        for arr in self._arrays:
            try:
                dxva2.DestroyPhysicalMonitors(len(arr), arr)
            except Exception:
                pass
        self._arrays, self._handles, self.handle = [], [], None


def list_monitors():
    out = []
    m = DDCMonitor(0)
    try:
        m.open()
    except RuntimeError:
        return out
    out = [{"index": i, "desc": pm.szPhysicalMonitorDescription}
           for i, pm in enumerate(m._handles)]
    m.close()
    return out


def keep_awake(on: bool) -> None:
    """爬升期间阻止系统/显示器休眠；结束后恢复。"""
    flags = ES_CONTINUOUS | (ES_SYSTEM_REQUIRED | ES_DISPLAY_REQUIRED if on else 0)
    try:
        kernel32.SetThreadExecutionState(flags)
    except Exception:
        pass


def nudge_input() -> None:
    """最小的模拟鼠标移动：让系统认为有人操作，显示器和会话都会醒。指针只抖 1 像素。"""
    try:
        user32.mouse_event(MOUSEEVENTF_MOVE, 1, 0, 0, 0)
        user32.mouse_event(MOUSEEVENTF_MOVE, -1, 0, 0, 0)
    except Exception:
        pass


def display_off() -> None:
    """关屏（调试用）。"""
    user32.SendMessageW(HWND_BROADCAST, WM_SYSCOMMAND, SC_MONITORPOWER, 2)


def force_display_on(mon: DDCMonitor, timeout: float = 25.0, probe_state=None) -> bool:
    """强制把屏幕点亮，等到 DDC/CI 真的能读到亮度为止。

    顺序：先叫醒系统/显示器 → 广播"显示器开" → 模拟输入 → DDC 电源指令 → 轮询确认。
    返回 True 表示确认屏幕已亮且可调。
    """
    keep_awake(True)
    t0 = time.monotonic()
    attempt = 0
    while True:
        attempt += 1
        try:
            user32.SendMessageW(HWND_BROADCAST, WM_SYSCOMMAND, SC_MONITORPOWER, -1)
        except Exception:
            pass
        nudge_input()
        mon.power_on()
        time.sleep(0.9)
        cur = mon.get_brightness()
        if curve_ok := (cur is not None):
            log(f"屏幕已点亮（第 {attempt} 次尝试，当前亮度 {cur}），耗时 {time.monotonic()-t0:.1f}s")
            return True
        if probe_state is not None:
            probe_state["attempt"] = attempt
        if time.monotonic() - t0 >= timeout:
            return False
        try:                                    # 再补一发 DDC 电源指令 + 输入，稍微用力点
            user32.SetCursorPos(*get_cursor_pos())
        except Exception:
            pass
        time.sleep(0.6)


def get_cursor_pos():
    pt = wintypes.POINT()
    user32.GetCursorPos(ctypes.byref(pt))
    return pt.x, pt.y


# --------------------------------------------------------------------------- 爬升

def ramp_seconds(start_time: str, end_time: str) -> int:
    t1, t2 = parse_hhmm(start_time), parse_hhmm(end_time)
    d1 = dt.timedelta(hours=t1.hour, minutes=t1.minute, seconds=t1.second)
    d2 = dt.timedelta(hours=t2.hour, minutes=t2.minute, seconds=t2.second)
    delta = (d2 - d1).total_seconds()
    if delta <= 0:
        delta += 24 * 3600
    return int(delta)


def parse_hhmm(text: str) -> dt.time:
    h, m = str(text).strip().split(":")[:2]
    return dt.time(int(h), int(m))


def write_state(**kw) -> None:
    try:
        with open(STATE_PATH, "w", encoding="utf-8") as f:
            json.dump(kw, f, ensure_ascii=False)
    except OSError:
        pass


def read_state():
    try:
        with open(STATE_PATH, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def run_ramp(cfg: dict, seconds: int | None = None, tick: float | None = None,
             on_tick=None, label: str = "爬升", restore: bool = False,
             abort: threading.Event | None = None, progress=None,
             hold: bool | None = None) -> bool:
    """点亮屏幕 → 背光爬升 → 到顶后保持。返回 True 表示正常跑完（False = 被手动关闭）。"""
    total = int(seconds) if seconds else ramp_seconds(cfg["start_time"], cfg["end_time"])
    step = float(tick or cfg.get("tick_seconds", 5))
    start_lv = int(cfg.get("start_level", 5))
    max_lv = int(cfg.get("max_level", 100))
    curve = float(cfg.get("curve", 1.0)) or 1.0

    if os.path.exists(STOP_PATH):
        os.remove(STOP_PATH)

    mon = DDCMonitor(cfg.get("monitor_index", 0)).open()
    stopped = False
    try:
        before = mon.get_brightness() if restore else None
        if before is None and not restore:
            before = mon.get_brightness()
        # 1) 先强制点亮
        ok = force_display_on(mon, timeout=float(cfg.get("wake_timeout", 25)))
        if not ok:
            log("⚠ 强制点亮超时：显示器可能被物理关掉了，或者 DDC/CI 被禁用。仍然继续尝试调亮度。")

        # 2) 背光爬升
        keep_awake(True)
        mon.set_brightness(start_lv)
        log(f"{label}开始: {start_lv} → {max_lv}，共 {total}s，每 {step}s 一档，曲线 {curve}"
            f"（显示器: {mon.desc}）")
        t_start = dt.datetime.now()
        t0 = time.monotonic()
        last = start_lv
        while True:
            if (abort is not None and abort.is_set()) or os.path.exists(STOP_PATH):
                stopped = True
                break
            elapsed = time.monotonic() - t0
            if elapsed >= total:
                break
            frac = min(1.0, (elapsed / total) ** curve)
            target = int(round(start_lv + (max_lv - start_lv) * frac))
            if target != last:
                if not mon.set_brightness(target):
                    log(f"写入亮度 {target} 失败（可能又进省电态），重试点亮")
                    force_display_on(mon, timeout=8)
                    mon.set_brightness(target)
                last = target
            write_state(pid=os.getpid(), started=t_start.isoformat(timespec="seconds"),
                        total_seconds=total, label=label, level=last,
                        elapsed=int(elapsed), frac=round(frac, 4), phase="ramp")
            if on_tick:
                on_tick(last, max(0.0, total - elapsed))
            if progress:
                progress(frac)
            time.sleep(step)

        mon.set_brightness(max_lv)

        # 到顶不马上结束：保持最高亮度，直到用户手动关闭（ESC / 点一下 / Ctrl+Alt+Q / --stop）
        keep_holding = (bool(cfg.get("hold", True)) if hold is None else hold) and not stopped
        if keep_holding:
            limit = max(0.0, float(cfg.get("hold_max_minutes", 0) or 0)) * 60
            desc = f"最长 {limit / 60:g} 分钟" if limit else "直到手动关闭"
            log(f"已到最高亮度 {max_lv}，进入保持（{desc}）——ESC / 点一下 / Ctrl+Alt+Q 关闭")
            h0 = time.monotonic()
            while True:
                if (abort is not None and abort.is_set()) or os.path.exists(STOP_PATH):
                    stopped = True
                    break
                held = time.monotonic() - h0
                if limit and held >= limit:
                    log("保持到上限，自动结束")
                    break
                if not mon.set_brightness(max_lv):
                    log("保持期间亮度写入失败，重试点亮")
                    force_display_on(mon, timeout=8)
                    mon.set_brightness(max_lv)
                write_state(pid=os.getpid(), started=t_start.isoformat(timespec="seconds"),
                            total_seconds=total, label=label, level=max_lv,
                            elapsed=total, frac=1.0, phase="hold", held=int(held))
                if on_tick:
                    on_tick(max_lv, 0.0)
                time.sleep(2)

        log(f"{label}{'被手动关闭' if stopped else '完成'}，当前亮度 {mon.get_brightness()}")
        if restore and before is not None:
            mon.set_brightness(before)
            log(f"已复原亮度到 {before}")
        return not stopped
    finally:
        keep_awake(False)
        mon.close()
        try:
            os.remove(STATE_PATH)
        except OSError:
            pass


def stop_run(wait: float = 4.0) -> None:
    open(STOP_PATH, "w").close()
    t0 = time.time()
    while os.path.exists(STATE_PATH) and time.time() - t0 < wait:
        time.sleep(0.3)
    st = read_state()
    if st and isinstance(st.get("pid"), int):
        subprocess.run(["taskkill", "/PID", str(st["pid"]), "/F"], capture_output=True)
        log(f"强制结束进程 {st['pid']}")
    try:
        os.remove(STATE_PATH)
    except OSError:
        pass
    try:
        os.remove(STOP_PATH)
    except OSError:
        pass
    print("已停止。")


# --------------------------------------------------------------------------- 日光层

def _lerp_color(c1, c2, f):
    f = max(0.0, min(1.0, f))
    return "#%02x%02x%02x" % tuple(int(round(a + (b - a) * f)) for a, b in zip(c1, c2))


def start_global_hotkey(on_trigger, combo_vk=VK_Q) -> dict:
    """注册全局热键 Ctrl+Alt+Q（即使焦点不在日光层上也能退出）。"""
    info = {"thread_id": None, "registered": False}

    def loop():
        info["thread_id"] = kernel32.GetCurrentThreadId()
        if not user32.RegisterHotKey(None, 1, MOD_CONTROL | MOD_ALT, combo_vk):
            log("全局热键 Ctrl+Alt+Q 注册失败（被别的程序占了），请用 ESC 退出")
            return
        info["registered"] = True
        msg = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            if msg.message == WM_HOTKEY:
                log("收到全局热键 Ctrl+Alt+Q，强制退出")
                on_trigger()
                break
        user32.UnregisterHotKey(None, 1)

    t = threading.Thread(target=loop, daemon=True, name="hotkey")
    t.start()
    time.sleep(0.2)
    return info


def run_overlay(cfg: dict, abort: threading.Event, total: int, label: str = "",
                on_progress=None) -> None:
    """全屏日光层：颜色由暖橙渐变到白。ESC / 点一下 / Ctrl+Alt+Q 退出（=中止爬升）。"""
    import tkinter as tk

    c1 = tuple(cfg.get("overlay_start_color", DEFAULT_CONFIG["overlay_start_color"]))
    c2 = tuple(cfg.get("overlay_end_color", DEFAULT_CONFIG["overlay_end_color"]))
    root = tk.Tk()

    def _tk_callback_error(exc, val, tb):          # Tk 回调里的异常默认只往 stderr 打，pythonw 下会丢
        log_traceback("Tk 回调", exc, val, tb)

    root.report_callback_exception = _tk_callback_error
    root.title("日出光闹钟")
    root.configure(bg=_lerp_color(c1, c2, 0))
    root.attributes("-topmost", True)
    try:
        root.attributes("-fullscreen", True)
    except tk.TclError:
        pass
    canvas = tk.Canvas(root, highlightthickness=0, bd=0, bg=_lerp_color(c1, c2, 0))
    canvas.pack(fill="both", expand=True)
    hint = canvas.create_text(0, 0, text="", fill="#333333", font=("Microsoft YaHei", 16))

    def show_hint(text):
        canvas.itemconfigure(hint, text=text)
        canvas.coords(hint, root.winfo_screenwidth() // 2, root.winfo_screenheight() - 80)

    show_hint("日出光闹钟 · 按 ESC 或 Ctrl+Alt+Q 退出")
    try:
        root.focus_force()
        root.lift()
    except tk.TclError:
        pass

    def quit_overlay(*_):
        log("日光层退出（用户操作或流程结束）")
        abort.set()
        try:
            root.destroy()
        except tk.TclError:
            pass

    root.bind("<Escape>", quit_overlay)
    root.bind("<Button>", quit_overlay)
    root.protocol("WM_DELETE_WINDOW", quit_overlay)
    start_global_hotkey(quit_overlay)

    t0 = time.monotonic()
    state = {"frac": 0.0, "done_at": None}

    def tick_ui():
        if abort.is_set():
            quit_overlay()
            return
        el = time.monotonic() - t0
        frac = min(1.0, (el / total) ** float(cfg.get("curve", 1.0) or 1.0)) if total else 1.0
        state["frac"] = frac
        if on_progress:
            on_progress(frac)
        col = _lerp_color(c1, c2, frac)
        try:
            canvas.configure(bg=col)
            root.configure(bg=col)
        except tk.TclError:
            return
        if el >= total:
            if state["done_at"] is None:          # 到顶了：提示“怎么关”
                state["done_at"] = el
                show_hint("已到最高亮度 · 按 ESC 或点一下关闭")
            elif el - state["done_at"] > 25:
                show_hint("")
        elif el > 12:
            show_hint("")
        root.after(300, tick_ui)

    # 保险：只在“不会无限保持”的情况下才自动关。
    # 保持模式若设为不限时，就靠 ESC / 点一下 / Ctrl+Alt+Q 手动关闭（这是设计意图）。
    hold_on = bool(cfg.get("hold", True))
    hold_limit = max(0.0, float(cfg.get("hold_max_minutes", 0) or 0)) * 60 if hold_on else 0.0
    if not (hold_on and hold_limit == 0):
        root.after(int((total + hold_limit + 120) * 1000), quit_overlay)
    root.after(300, tick_ui)
    root.mainloop()
    abort.set()


# --------------------------------------------------------------------------- 计划任务

def next_run_time(cfg: dict):
    try:
        t = parse_hhmm(cfg.get("task_time", cfg.get("start_time", "07:00")))
    except ValueError:
        return None
    days = set(cfg.get("task_weekdays") or DEFAULT_CONFIG["task_weekdays"])
    now = dt.datetime.now()
    for i in range(0, 8):
        d = now + dt.timedelta(days=i)
        if WEEKDAYS[d.weekday()][0] not in days:
            continue
        cand = dt.datetime.combine(d.date(), t)
        if cand > now:
            return cand
    return None


TASK_DAY_TAGS = {"MON": "Monday", "TUE": "Tuesday", "WED": "Wednesday", "THU": "Thursday",
                 "FRI": "Friday", "SAT": "Saturday", "SUN": "Sunday"}


def task_xml(cfg: dict) -> str:
    days = cfg.get("task_weekdays") or DEFAULT_CONFIG["task_weekdays"]
    day_tags = "".join(f"<{TASK_DAY_TAGS[d]}/>" for d in days if d in TASK_DAY_TAGS)
    t = parse_hhmm(cfg.get("task_time", cfg.get("start_time", "07:00")))
    boundary = f"{(dt.date.today() + dt.timedelta(days=1)).isoformat()}T{t.hour:02d}:{t.minute:02d}:00"
    script = os.path.join(BASE, "sunrise_alarm.py")
    pythonw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    if not os.path.exists(pythonw):
        pythonw = sys.executable
    user = f"{os.environ.get('USERDOMAIN', '')}\\{os.environ.get('USERNAME', '')}".strip("\\")
    return f"""<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Description>日出光闹钟：强制点亮屏幕 + 背光缓亮 + 全屏日光层（ESC 可退出）</Description>
  </RegistrationInfo>
  <Triggers>
    <CalendarTrigger>
      <StartBoundary>{boundary}</StartBoundary>
      <Enabled>true</Enabled>
      <ScheduleByWeek>
        <DaysOfWeek>{day_tags}</DaysOfWeek>
        <WeeksInterval>1</WeeksInterval>
      </ScheduleByWeek>
    </CalendarTrigger>
  </Triggers>
  <Principals>
    <Principal id="Author">
      <UserId>{user}</UserId>
      <LogonType>InteractiveToken</LogonType>
      <RunLevel>LeastPrivilege</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <AllowHardTerminate>true</AllowHardTerminate>
    <StartWhenAvailable>true</StartWhenAvailable>
    <WakeToRun>true</WakeToRun>
    <ExecutionTimeLimit>PT2H</ExecutionTimeLimit>
    <Enabled>true</Enabled>
    <Hidden>false</Hidden>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>{pythonw}</Command>
      <Arguments>"{script}" --run</Arguments>
      <WorkingDirectory>{BASE}</WorkingDirectory>
    </Exec>
  </Actions>
</Task>
"""


def install_task(cfg: dict) -> tuple[bool, str]:
    xml_path = os.path.join(BASE, "task-definition.xml")
    with open(xml_path, "w", encoding="utf-16") as f:
        f.write(task_xml(cfg))
    r = subprocess.run(["schtasks", "/Create", "/TN", TASK_NAME, "/XML", xml_path, "/F"],
                       capture_output=True, text=True, encoding="gbk", errors="replace")
    ok = r.returncode == 0
    msg = ((r.stdout or "") + (r.stderr or "")).strip()
    log(f"计划任务{'已注册' if ok else '注册失败'}: {msg}")
    return ok, msg


def remove_task() -> tuple[bool, str]:
    r = subprocess.run(["schtasks", "/Delete", "/TN", TASK_NAME, "/F"],
                       capture_output=True, text=True, encoding="gbk", errors="replace")
    return r.returncode == 0, ((r.stdout or "") + (r.stderr or "")).strip()


def task_query() -> str:
    r = subprocess.run(["schtasks", "/Query", "/TN", TASK_NAME, "/V", "/FO", "LIST"],
                       capture_output=True, text=True, encoding="gbk", errors="replace")
    return ((r.stdout or "") + (r.stderr or "")).strip()


# --------------------------------------------------------------------------- CLI

def load_config() -> dict:
    cfg = dict(DEFAULT_CONFIG)
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, encoding="utf-8") as f:
                cfg.update(json.load(f))
        except (OSError, ValueError) as e:
            log(f"config.json 读取失败，用默认值: {e}")
    return cfg


def save_config(cfg: dict) -> None:
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


def run_alarm(cfg: dict, seconds=None, tick=None, restore=False, label="爬升",
              overlay: bool | None = None, hold: bool | None = None) -> None:
    """一次完整流程：点亮 + 爬升 + 到顶保持（+ 日光层），日光层跑在主线程。"""
    use_overlay = cfg.get("overlay", True) if overlay is None else overlay
    total = int(seconds) if seconds else ramp_seconds(cfg["start_time"], cfg["end_time"])
    abort = threading.Event()

    def worker():
        try:
            run_ramp(cfg, seconds=seconds, tick=tick, label=label, restore=restore,
                     abort=abort, hold=hold)
        except Exception as e:                                    # noqa: BLE001
            log_traceback("爬升线程", type(e), e, e.__traceback__)
            log(f"运行出错: {e}")
        finally:
            abort.set()

    th = threading.Thread(target=worker, daemon=True, name="ramp")
    th.start()
    if use_overlay:
        run_overlay(cfg, abort, total, label=label)
    else:
        th.join()
    th.join(timeout=5)
    if th.is_alive():                       # 爬升线程卡住了也不能让进程吊着
        log("爬升线程未按时结束，强制退出")
    if sys.stdout is not None:      # pythonw + 计划任务下 stdout 是 None，裸 flush() 会抛 AttributeError → 退出码 1
        sys.stdout.flush()


def cmd_status(cfg: dict) -> None:
    m = DDCMonitor(cfg.get("monitor_index", 0))
    cur = "?"
    try:
        m.open()
        b = m.get_brightness()
        cur = f"{b} / {m.maximum}  ({m.desc})" if b is not None else f"读不到（屏幕可能在省电态）  ({m.desc})"
    except RuntimeError as e:
        cur = f"读不到（{e}）"
    st = read_state()
    nxt = next_run_time(cfg)
    print(f"当前亮度 : {cur}")
    if st:
        if st.get("phase") == "hold":
            run_txt = f"保持中，亮度 {st.get('level')}（已保持 {st.get('held', 0)}s，等手动关闭）"
        else:
            run_txt = f"是，亮度 {st.get('level')}（{st.get('elapsed')}s / {st.get('total_seconds')}s）"
    else:
        run_txt = "否"
    print(f"正在爬升 : {run_txt}")
    hold_min = float(cfg.get("hold_max_minutes", 0) or 0)
    if not cfg.get("hold", True):
        hold_desc = "关"
    else:
        hold_desc = f"开（上限 {hold_min:g} 分钟）" if hold_min > 0 else "开（一直保持到手动关闭）"
    print(f"时间窗口 : {cfg['start_time']} → {cfg['end_time']}"
          f"（{ramp_seconds(cfg['start_time'], cfg['end_time']) // 60} 分钟），"
          f"{cfg['start_level']} → {cfg['max_level']}，"
          f"日光层 {'开' if cfg.get('overlay', True) else '关'}，保持 {hold_desc}")
    print(f"下次触发 : {nxt:%Y-%m-%d %H:%M}（{','.join(cfg.get('task_weekdays', []))}）" if nxt else "下次触发 : 无")
    m.close()


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="日出光闹钟（强制点亮 + 背光缓亮 + 全屏日光层）")
    p.add_argument("--run", action="store_true", help="按 config.json 完整跑一次（计划任务用）")
    p.add_argument("--preview", type=float, metavar="SECONDS", help="把整段压缩到 N 秒预览")
    p.add_argument("--stop", action="store_true", help="停止正在跑的爬升")
    p.add_argument("--status", action="store_true", help="显示当前亮度/状态/计划任务")
    p.add_argument("--list", action="store_true", help="列出显示器")
    p.add_argument("--wake", action="store_true", help="只测试强制点亮屏幕")
    p.add_argument("--off", action="store_true", help="关屏（配合 --wake 测试）")
    p.add_argument("--install-task", action="store_true", help="注册/更新计划任务（要管理员）")
    p.add_argument("--remove-task", action="store_true", help="删除计划任务")
    p.add_argument("--set", type=int, metavar="N", help="直接把亮度设成 N（调试）")
    p.add_argument("--get", action="store_true", help="只打印当前亮度数字")
    p.add_argument("--start-level", type=int, help="覆盖起始亮度（仅本次）")
    p.add_argument("--max-level", type=int, help="覆盖最高亮度")
    p.add_argument("--curve", type=float, help="覆盖亮度曲线")
    p.add_argument("--tick", type=float, help="覆盖写入间隔秒数")
    p.add_argument("--monitor", type=int, help="覆盖显示器序号")
    p.add_argument("--keep", action="store_true", help="预览结束后不复原亮度")
    p.add_argument("--no-overlay", action="store_true", help="不铺全屏日光层")
    p.add_argument("--hold", action="store_true", help="到最高亮度后保持不结束（预览时也保持）")
    p.add_argument("--no-hold", action="store_true", help="到顶就结束，不保持")
    p.add_argument("--hold-minutes", type=float, help="保持上限分钟数；0=不限，直到手动关闭")
    args = p.parse_args(argv)
    cfg = load_config()
    for key, val in (("start_level", args.start_level), ("max_level", args.max_level),
                     ("curve", args.curve), ("tick_seconds", args.tick),
                     ("monitor_index", args.monitor)):
        if val is not None:
            cfg[key] = val
    if args.hold:
        cfg["hold"] = True
    if args.no_hold:
        cfg["hold"] = False
    if args.hold_minutes is not None:
        cfg["hold_max_minutes"] = args.hold_minutes

    if args.list:
        for m in list_monitors():
            print(f"[{m['index']}] {m['desc']}")
        return 0
    if args.off:
        display_off()
        print("已发送关屏指令。")
        return 0
    if args.wake:
        mon = DDCMonitor(cfg.get("monitor_index", 0)).open()
        ok = force_display_on(mon, timeout=float(cfg.get("wake_timeout", 25)))
        print("屏幕已点亮，DDC 可读。当前亮度:", mon.get_brightness() if ok else "仍读不到")
        mon.close()
        return 0 if ok else 1
    if args.get:
        mon = DDCMonitor(cfg.get("monitor_index", 0)).open()
        print(mon.get_brightness())
        mon.close()
        return 0
    if args.set is not None:
        mon = DDCMonitor(cfg.get("monitor_index", 0)).open()
        print("set:", mon.set_brightness(args.set), "now:", mon.get_brightness())
        mon.close()
        return 0
    if args.status:
        cmd_status(cfg)
        return 0
    if args.stop:
        stop_run()
        return 0
    if args.install_task:
        ok, msg = install_task(cfg)
        print(msg)
        return 0 if ok else 1
    if args.remove_task:
        ok, msg = remove_task()
        print(msg)
        return 0 if ok else 1
    if args.preview:
        if not args.hold and not args.no_hold:
            cfg["hold"] = False            # 预览默认不保持（除非显式 --hold），免得不小心挂一整晚
        run_alarm(cfg, seconds=int(args.preview), tick=max(0.2, args.preview / 40.0),
                  restore=not args.keep, label="预览",
                  overlay=False if args.no_overlay else True)
        return 0
    if args.run:
        run_alarm(cfg, label="爬升", overlay=False if args.no_overlay else None)
        return 0

    SunriseGUI(cfg).mainloop()
    return 0


# --------------------------------------------------------------------------- GUI

class SunriseGUI:
    def __init__(self, cfg: dict):
        import tkinter as tk
        from tkinter import ttk, messagebox

        self.tk, self.ttk, self.messagebox = tk, ttk, messagebox
        self.cfg = cfg
        self.child = None
        self.pythonw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
        if not os.path.exists(self.pythonw):
            self.pythonw = sys.executable

        root = tk.Tk()
        self.root = root
        root.title("日出光闹钟 · Sunrise Alarm")
        root.resizable(False, False)
        pad = {"padx": 8, "pady": 4}

        box = ttk.LabelFrame(root, text="时间与亮度")
        box.grid(row=0, column=0, sticky="ew", **pad)

        self.v_start = tk.StringVar(value=cfg["start_time"])
        self.v_end = tk.StringVar(value=cfg["end_time"])
        self.v_from = tk.StringVar(value=str(cfg["start_level"]))
        self.v_to = tk.StringVar(value=str(cfg["max_level"]))
        self.v_curve = tk.StringVar(value=str(cfg["curve"]))
        self.v_task_time = tk.StringVar(value=cfg.get("task_time", cfg["start_time"]))
        self.v_overlay = tk.BooleanVar(value=bool(cfg.get("overlay", True)))
        self.v_hold = tk.BooleanVar(value=bool(cfg.get("hold", True)))
        self.v_hold_min = tk.StringVar(value=str(cfg.get("hold_max_minutes", 0)))

        def field(parent, label, var, r, c, width=8):
            ttk.Label(parent, text=label).grid(row=r, column=c * 2, sticky="e", **pad)
            ttk.Entry(parent, textvariable=var, width=width).grid(row=r, column=c * 2 + 1, sticky="w", **pad)

        field(box, "开始时间", self.v_start, 0, 0)
        field(box, "到最高亮度", self.v_end, 0, 1)
        field(box, "起始亮度", self.v_from, 1, 0)
        field(box, "最高亮度", self.v_to, 1, 1)
        field(box, "亮度曲线", self.v_curve, 2, 0)
        field(box, "计划任务时刻", self.v_task_time, 2, 1)
        ttk.Checkbutton(box, text="爬升时铺全屏日光层（ESC 可退出）",
                        variable=self.v_overlay).grid(row=3, column=0, columnspan=4, sticky="w", **pad)
        ttk.Checkbutton(box, text="到顶后保持，直到手动关闭",
                        variable=self.v_hold).grid(row=4, column=0, columnspan=2, sticky="w", **pad)
        ttk.Label(box, text="保持上限(分钟,0=不限)").grid(row=4, column=2, sticky="e", **pad)
        ttk.Entry(box, textvariable=self.v_hold_min, width=8).grid(row=4, column=3, sticky="w", **pad)

        day_box = ttk.LabelFrame(root, text="哪几天跑计划任务")
        day_box.grid(row=1, column=0, sticky="ew", **pad)
        self.day_vars = {}
        for i, (code, name) in enumerate(WEEKDAYS):
            v = tk.BooleanVar(value=code in (cfg.get("task_weekdays") or []))
            self.day_vars[code] = v
            ttk.Checkbutton(day_box, text=name, variable=v).grid(row=0, column=i, **pad)

        btn = ttk.Frame(root)
        btn.grid(row=2, column=0, sticky="ew", **pad)
        ttk.Button(btn, text="预览 10 秒", command=lambda: self.start(preview=10)).grid(row=0, column=0, **pad)
        ttk.Button(btn, text="立即开始", command=self.start).grid(row=0, column=1, **pad)
        ttk.Button(btn, text="停止", command=self.stop).grid(row=0, column=2, **pad)
        ttk.Button(btn, text="读当前亮度", command=self.read_brightness).grid(row=0, column=3, **pad)
        ttk.Button(btn, text="只测点亮屏幕", command=self.test_wake).grid(row=0, column=4, **pad)

        tsk = ttk.Frame(root)
        tsk.grid(row=3, column=0, sticky="ew", **pad)
        ttk.Button(tsk, text="安装/更新计划任务（需管理员）", command=self.install).grid(row=0, column=0, **pad)
        ttk.Button(tsk, text="删除计划任务", command=self.remove).grid(row=0, column=1, **pad)
        ttk.Button(tsk, text="查看任务状态", command=self.show_task).grid(row=0, column=2, **pad)

        self.status = tk.StringVar(value="就绪。日光层里按 ESC / 点一下 / Ctrl+Alt+Q 可随时中止。")
        ttk.Label(root, textvariable=self.status, foreground="#036", wraplength=520).grid(
            row=4, column=0, sticky="w", **pad)

        root.after(500, self.tick)

    def pull(self) -> dict:
        c = dict(self.cfg)
        c["start_time"] = self.v_start.get().strip()
        c["end_time"] = self.v_end.get().strip()
        c["start_level"] = int(float(self.v_from.get()))
        c["max_level"] = int(float(self.v_to.get()))
        c["curve"] = float(self.v_curve.get())
        c["overlay"] = bool(self.v_overlay.get())
        c["hold"] = bool(self.v_hold.get())
        c["hold_max_minutes"] = float(self.v_hold_min.get() or 0)
        c["task_time"] = self.v_task_time.get().strip()
        c["task_weekdays"] = [k for k, v in self.day_vars.items() if v.get()]
        parse_hhmm(c["start_time"]); parse_hhmm(c["end_time"]); parse_hhmm(c["task_time"])
        return c

    def save(self) -> dict:
        self.cfg = self.pull()
        save_config(self.cfg)
        return self.cfg

    def _spawn(self, *extra):
        if self.child is not None and self.child.poll() is None:
            self.messagebox.showinfo("日出光闹钟", "已经有一个在跑了，先点「停止」。")
            return
        cmd = [self.pythonw, os.path.join(BASE, "sunrise_alarm.py"), *extra]
        flags = 0x00000008 if os.name == "nt" else 0        # DETACHED_PROCESS
        self.child = subprocess.Popen(cmd, cwd=BASE, creationflags=flags,
                                      stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                      stderr=subprocess.DEVNULL)

    def start(self, preview: int | None = None):
        try:
            cfg = self.save()
        except ValueError as e:
            self.messagebox.showerror("参数不对", f"时间格式要 HH:MM（例 07:00）。\n{e}")
            return
        extra = ["--preview", str(preview)] if preview else ["--run"]
        if not cfg.get("overlay", True):
            extra.append("--no-overlay")
        # 预览不保持（免得不小心挂一晚）；正式跑按勾选
        extra.append("--no-hold" if (preview or not cfg.get("hold", True)) else "--hold")
        self._spawn(*extra)
        self.status.set("预览中…（日光层按 ESC 退出）" if preview else "爬升中…（日光层按 ESC 退出）")

    def stop(self):
        self._spawn("--stop")
        self.status.set("已发送停止指令")

    def test_wake(self):
        self._spawn("--wake")
        self.status.set("正在测试强制点亮屏幕…")

    def read_brightness(self):
        try:
            m = DDCMonitor(self.cfg.get("monitor_index", 0)).open()
            b = m.get_brightness()
            self.status.set(f"当前亮度 {b} / {m.maximum}（{m.desc}）" if b is not None
                            else f"读不到亮度——屏幕可能在省电态（{m.desc}），试试「只测点亮屏幕」")
            m.close()
        except RuntimeError as e:
            self.status.set(f"读不到亮度：{e}")

    def install(self):
        try:
            cfg = self.save()
        except ValueError as e:
            self.messagebox.showerror("参数不对", str(e))
            return
        ok, msg = install_task(cfg)
        if ok:
            nxt = next_run_time(cfg)
            self.messagebox.showinfo("日出光闹钟",
                                     f"计划任务已装好。\n下次：{nxt:%Y-%m-%d %H:%M}\n\n"
                                     "若提示权限不足，请右键用管理员身份运行 安装定时任务.bat。")
        else:
            self.messagebox.showerror("装不上", msg or "schtasks 返回失败，试试管理员运行。")

    def remove(self):
        if self.messagebox.askyesno("日出光闹钟", f"删除计划任务 {TASK_NAME}？"):
            ok, msg = remove_task()
            self.messagebox.showinfo("日出光闹钟", "已删除。" if ok else (msg or "删除失败"))

    def show_task(self):
        self.messagebox.showinfo("计划任务状态", task_query()[:1500])

    def tick(self):
        st = read_state()
        if st:
            if st.get("phase") == "hold":
                self.status.set(f"保持中… 亮度 {st.get('level')}，已保持 {st.get('held')}s"
                                "（按 ESC / 点一下 / Ctrl+Alt+Q 关闭）")
            else:
                self.status.set(f"爬升中… 亮度 {st.get('level')}，已过 {st.get('elapsed')}s / "
                                f"{st.get('total_seconds')}s（日光层按 ESC 退出）")
        self.root.after(700, self.tick)

    def mainloop(self):
        self.root.mainloop()


if __name__ == "__main__":
    install_crash_capture()
    log(f"启动: {' '.join(sys.argv[1:]) or '(GUI)'}  python={sys.version.split()[0]}  stdout={'有' if sys.stdout is not None else 'None'}")
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except BaseException as e:                     # 兜底：哪怕入口处炸了也要留痕
        log_traceback("入口", type(e), e, e.__traceback__)
        log(f"入口未捕获异常: {type(e).__name__}: {e}")
        sys.exit(1)
