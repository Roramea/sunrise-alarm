# 日出光闹钟 · Sunrise Alarm

拿显示器当光源的光闹钟：到点先把屏幕点亮，再把背光亮度从很低缓慢爬到最高，
把睡觉的位置"晒"醒。**不依赖 Twinkle Tray、不依赖 OpenClaw**，换台 Windows 电脑也能用。

## 它怎么工作

| 层 | 做法 |
|---|---|
| 触发 | Windows 计划任务（工作日 07:00，勾了 `WakeToRun`，睡眠中也能被叫醒） |
| **强制点亮** | 三步硬拉：广播 `SC_MONITORPOWER -1` + 模拟一次最小鼠标移动 + DDC/CI 电源指令 `0xD6=1`，然后**轮询到 DDC 真能读到亮度才往下走**（屏幕没亮，调亮度没意义） |
| 渐亮 | 直接走 **DDC/CI**（Windows 自带 `dxva2.dll`）读写显示器背光，07:00→07:30 从 5 爬到 100 |
| **日光层** | 爬升期间铺一层全屏窗口，颜色由暖橙 `#FF7018` 渐变到白，既是光源也是进度条 |
| **到顶保持** | 爬到最高亮度后**不结束**，一直亮着（像闹钟的最后一响），直到你手动关闭；也可设上限分钟数 |
| **逃生** | 日光层里随时 **ESC / 点一下 / 全局热键 `Ctrl+Alt+Q`** 关闭（热键是全局的，窗口失焦也能退）；另有 `测试-停止.bat` |
| 保险 | 爬升期间用 `SetThreadExecutionState` 顶住系统/显示器休眠；日光层自带走火保险（限时保持/不保持时会超时自关，不限时保持只认手动关闭）；结束时确认亮度=最高 |

DDC/CI 就是 Twinkle Tray 那套调节原理，这里用 `ctypes` 自己实现了一遍，
所以本目录不依赖任何第三方软件，只要显示器开着 DDC/CI（大部分显示器默认开）。

## 文件

| 文件 | 说明 |
|---|---|
| `sunrise_alarm.py` | 主程序：GUI + 命令行，单文件零依赖 |
| `启动GUI.bat` | 双击打开图形界面 |
| `测试-快速预览.bat` | **测效果首选**：顶部改 4 个参数→保存→双击，秒级演完一遍爬升，跑完自动复原亮度 |
| `测试-模拟到点.bat` | 立即触发真计划任务（假装现在就是 7 点），默认跑 60 秒后自动停并复原 |
| `测试-停止.bat` | 双击停掉正在跑的爬升 |
| `安装定时任务.bat` | 右键→以管理员身份运行，注册/更新计划任务 |
| `config.json` | 配置（GUI 改动会自动写回） |
| `task-definition.xml` | 上次生成的计划任务定义（自动生成） |
| `sunrise_alarm.log` | 运行日志（UTF-8；用记事本或 `Get-Content -Encoding UTF8` 看） |
| `state.json` | 运行中才有，记录进程号/剩余时长 |

## 环境要求

- Windows 10 / 11
- Python 3.8+（本机是 3.12），只需标准库，**不用 pip 装任何东西**
- 显示器支持 DDC/CI（外接显示器基本都支持；个别型号要在 OSD 菜单里手动打开）

## 怎么用

**图形界面**：双击 `启动GUI.bat`。里面能改开始/结束时间、起始/最高亮度、曲线、
哪几天跑，还有「预览 10 秒」「立即开始」「停止」「读当前亮度」和计划任务的装/删/查。

**命令行**：

```powershell
python sunrise_alarm.py --run            # 完整跑一次（计划任务调的就是这个）
python sunrise_alarm.py --preview 15     # 15 秒看完整效果（跑完自动复原亮度）
python sunrise_alarm.py --status         # 当前亮度 / 是否在跑 / 下次触发
python sunrise_alarm.py --get            # 只打印当前亮度数字
python sunrise_alarm.py --wake           # 只做「强制点亮屏幕」并报告结果
python sunrise_alarm.py --off            # 关屏（配合 --wake 做测试）
python sunrise_alarm.py --stop           # 停掉正在跑的爬升（亮度停在原地）
python sunrise_alarm.py --list           # 列出显示器
python sunrise_alarm.py --set 40         # 直接把亮度设成 40（调试）
python sunrise_alarm.py --install-task   # 注册/更新计划任务（要管理员）
python sunrise_alarm.py --remove-task    # 删掉计划任务
```

**临时覆盖参数**（只对本次运行有效，不改 `config.json`）：
`--start-level N` `--max-level N` `--curve X` `--tick SEC` `--monitor N` `--keep`（预览后不复原亮度）`--no-overlay`（不铺日光层）
`--hold` / `--no-hold`（到顶后是否保持；**预览默认不保持**）`--hold-minutes N`（保持上限分钟，0=不限直到手动关闭）。

例：`python sunrise_alarm.py --preview 20 --start-level 8 --max-level 80 --curve 1.4`

## config.json

```json
{
  "start_time": "07:00",          // 开始爬升
  "end_time": "07:30",            // 到最高亮度
  "start_level": 5,               // 起始亮度（0 全黑看不见，建议 3~10）
  "max_level": 100,
  "curve": 1.0,                   // 1=线性；>1 前段更慢，<1 前段更猛
  "tick_seconds": 5,              // 每隔几秒写一次亮度
  "overlay": true,                // 爬升期间铺全屏日光层（ESC / Ctrl+Alt+Q 退出）
  "hold": true,                   // 到顶后保持，直到手动关闭（像闹钟最后一响）
  "hold_max_minutes": 0,          // 保持上限（分钟）；0 = 不限，一直保持到手动关闭
  "overlay_start_color": [255, 112, 24],   // 日光层起始色（暖橙）
  "overlay_end_color": [255, 255, 255],    // 日光层结束色（白）
  "wake_timeout": 25,             // 强制点亮最多等多少秒
  "monitor_index": 0,             // 第几台显示器，0 起
  "task_time": "07:00",           // 计划任务触发时刻
  "task_weekdays": ["MON","TUE","WED","THU","FRI"]
}
```

## 换台电脑怎么搬

1. 整个 `sunrise-alarm` 文件夹拷过去（Python 装好、勾了 "Add python.exe to PATH"）。
2. 双击 `安装定时任务.bat`，以管理员身份跑一次（脚本会重新按新路径注册任务）。
3. `python sunrise_alarm.py --preview 15` 试一下亮不亮；不亮多半是显示器 DDC/CI 没开。

## 常见问题

- **到顶了怎么关掉？** 日光层还在的时候：**ESC / 点一下屏幕 / 全局热键 `Ctrl+Alt+Q`** 三个都能关；
  日光层已经没了（比如关了日光层）就双击 `测试-停止.bat`，或跑 `python sunrise_alarm.py --stop`。
- **不想它一直亮着**：config.json 里 `hold` 设 false（到顶就结束），或 `hold_max_minutes` 给个上限（比如 60）。

- **屏幕不亮 / 读不到亮度**：先用 `python sunrise_alarm.py --wake` 单测这一步；
  显示器 OSD 里把 DDC/CI 打开；VGA/DVI 线可能不支持，用 HDMI/DP。
- **显示器是被电源键物理关掉的**：软件救不回来，只能按一下电源键（这一条谁也做不到）。
- **日光层卡住退不出去**：ESC / 点一下 / `Ctrl+Alt+Q` 三个都能退；再不行双击 `测试-停止.bat`。
- **到点屏幕不亮**：确认任务里 `WakeToRun` 是 true（本脚本已写好）；
  另外系统要允许唤醒定时器：`powercfg /query SCHEME_CURRENT SUB_SLEEP RTCWAKE`。
- **任务装不上**：`schtasks` 需要管理员，用 `安装定时任务.bat` 提权跑。
- **想临时关掉**：`schtasks /Change /TN SunriseAlarm /DISABLE`（或 GUI 里的删除）。
- **日志乱码**：文件是 UTF-8，用 `Get-Content -Encoding UTF8` 看。

## 安全边界

只写显示器亮度（0~100）与发送"点亮显示器"消息；不改电源计划、不装驱动、不联网、不自启常驻。
计划任务随时可用 `--remove-task` 或 `schtasks /Delete /TN SunriseAlarm /F` 移除。

## 崩溃排查：crash.log（2026-09-30 加）

计划任务用 `pythonw.exe` 跑 → **没有 stderr**，未捕获异常会静默退出码 1，只留下「任务失败」四个字。
所以脚本启动时会先装崩溃捕获，把 traceback 落到同目录 `crash.log`：

- `faulthandler.enable(all_threads=True)` —— 硬崩溃/卡死也能 dump
- `sys.excepthook` —— 主线程未捕获异常
- `threading.excepthook` —— 爬升线程 / 热键线程
- `root.report_callback_exception` —— Tk 回调里的异常（pythonw 下本来会被吞掉）
- 入口 try/except 兜底，仍以 1 退出，但先留痕

另外每次启动会在 `sunrise_alarm.log` 记一行
`启动: <参数>  python=<版本>  stdout=有/None`，用来分辨是控制台还是计划任务环境。

**怎么用**：正常时 `crash.log` 是 0 字节空文件；只要它有内容，直接看里面的 traceback。
**已验证**：合成测试下主线程与子线程异常都正确落盘；`pythonw.exe --status`（stdout=None，与计划任务同环境）退出码 0。
