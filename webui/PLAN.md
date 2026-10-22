# 光闹钟 · 网页版 GUI（pywebview）v1 计划

## 目标
在**不重写任何背光/DDC 逻辑**的前提下，用 pywebview 起一个原生窗口，界面用本地网页做，
功能对齐现有 tkinter 版。所有真正的动作（点亮 / 爬升 / 预览 / 停止 / 读数 / 计划任务）
一律**子进程调用 `sunrise_alarm.py` 的 CLI**，本壳只做界面 + 读写 config.json。

## 架构
```
启动GUI-网页版.bat
      └─ pythonw sunrise_gui_web.py            (pywebview 主进程 = 壳)
              ├─ webui/index.html + app.js     (界面，纯本地，无外网 CDN)
              ├─ webui/style.css               (样式)
              ├─ JS ↔ Python: window.pywebview.api.*   (pywebview JS-API)
              │      save_config / load_config          直接读写 config.json
              │      get_brightness / get_state         读 CLI(--get) / state.json
              │      preview / start / stop / wake      子进程调 CLI
              │      install_task / remove_task / task_query / list_monitors
              └─ 子进程 → python sunrise_alarm.py --get / --preview N / --stop / ...
```

### 分层边界（硬规矩）
- **DDC/背光/日光层/爬升算法：只属于 `sunrise_alarm.py`，本壳一行都不碰。**
- 本壳只允许：① 读写 `config.json`；② 起/停子进程跑 CLI；③ 读 `state.json` 显示状态。
- 不 import `sunrise_alarm` 的核心函数来做背光操作（只做轻量 HH:MM 校验）。

## 文件清单
| 文件 | 作用 |
|---|---|
| `webui/PLAN.md` | 本计划 |
| `webui/index.html` | 界面结构（中文） |
| `webui/style.css` | 样式（日出配色，离线） |
| `webui/app.js` | 前端逻辑：表单↔config、按钮、轮询亮度/状态 |
| `sunrise_gui_web.py` | pywebview 主程序 + JS-API（壳） |
| `启动GUI-网页版.bat` | 双击入口（CRLF + GBK/cp936） |
| `启动GUI.bat` | **旧 tkinter 入口，原样保留** |

## 功能对齐（v1）
- 读写 config.json：`start_time / end_time / start_level / max_level / curve /
  curve_points / overlay_mode(off|white|sunrise|sky) / tick_seconds /
  task_time / task_weekdays / monitor_index`，另保留 `overlay / hold / hold_max_minutes`。
- 「立即预览」（可选秒数，默认 10s，预览不保持）与「停止」→ CLI `--preview N --no-hold` / `--stop`。
- 「当前亮度」实时显示 → 后台线程轮询 `--get`，前端每 2s 刷新。
- 「运行状态」→ 读 `state.json`（爬升中/保持中的亮度与进度）。
- 计划任务：装/删/查 → CLI `--install-task` / `--remove-task` + `schtasks /Query`（只按钮触发，不动任务本身）。
- 保存/重新载入 config；附加：只测点亮（`--wake`）、列显示器（`--list`）、跳过下次（`--skip-next`）。

## 里程碑
1. **M0** 计划（本文件）+ 依赖核查：`pip install --user pywebview` ✅、WebView2 运行时 ✅。
2. **M1** 壳跑起来：pywebview 打开本地页，JS-API 通（load/save config）。
3. **M2** 按钮接线：preview / stop / wake / 任务装删查。
4. **M3** 实时：亮度轮询 + state.json 状态。
5. **M4** 入口 .bat（CRLF+GBK）、真跑验收、旧 tkinter GUI 回归。

## 验收（真跑）
- 能启动窗口（`--smoke` 自检也会验证开窗+正常关闭）。
- 读出 config 的值显示在界面；改一项保存后 config.json 确实变了。
- 触发一次短 preview（如 4s）能跑（用 `--no-overlay` 免得占满屏）。
- 窗口能正常关闭。
- 旧 `启动GUI.bat`（tkinter）仍能启动。
