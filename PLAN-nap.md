# 小睡（Nap）功能 · 设计方案

> 2026-10-19 鱼片提的：「光闹钟项目可以加一个 nap 功能」。先规划，不动手写代码。
> 现状：2026-10-14 临时用 `nap-alarm-preview.bat`（screen_power on + `--preview 600 --keep`）凑过一晚，
> 但那不是小睡——`--preview` 是把整段爬升**压缩**到 N 秒、tick 固定成 N/40、默认不保持，
> 语义完全不对。本方案把它正规化。

## 1. 要解决的场景

- 一句「我睡 20 分钟」→ 20 分钟后叫醒，**醒来是慢慢亮的**，不是一刀切全亮。
- 睡着期间屏幕**不该亮着**（除非主动要暗光夜灯）。
- 中途能取消；叫醒后还能「再眯 5 分钟」。

## 2. 三个参数决定形态

| 场景 | 参数 | 行为 |
|---|---|---|
| 标准小睡（默认） | `--nap 20`，ramp=10 | 前 10 分钟黑屏待机，最后 10 分钟从 1 爬到 100，到顶保持 |
| 慢醒 / 要暗光 | `--nap 20 --nap-ramp 20` | 全程渐亮（当夜灯用） |
| 纯闹钟 | `--nap 20 --nap-ramp 0` | 到点直接点亮 100 + 日光层 |

## 3. config.json 新增（全部带默认值，老配置不受影响）

```json
{
  "nap_minutes": 20,
  "nap_ramp_minutes": 10,
  "nap_start_level": 1,
  "nap_max_level": 100,
  "nap_hold_minutes": 30,
  "nap_curve": 1.0
}
```

`nap_hold_minutes: 30` 是保险：防止叫醒后没人管，屏幕亮一整天。

## 4. CLI（main() 里加分支）

```powershell
python sunrise_alarm.py --nap 20                # 20 分钟后叫醒，最后 10 分钟渐亮
python sunrise_alarm.py --nap 20 --nap-ramp 20  # 全程渐亮
python sunrise_alarm.py --nap 20 --nap-ramp 0   # 到点直接全亮
python sunrise_alarm.py --nap-cancel            # 取消（等同 --stop，但连待机阶段也能停）
python sunrise_alarm.py --status                # 状态里多一行：小睡中，还有 12:30 叫醒
```

## 5. 实现落点（Phase A，改约 120 行）

1. **`keep_awake(on, display=True)`**：加参数。小睡待机阶段只顶 `ES_SYSTEM_REQUIRED`，
   **不**顶 `ES_DISPLAY_REQUIRED`，屏幕该灭就灭。
2. **新增 `run_nap(cfg, total_min, ramp_min)`**：
   - 阶段 1 待机：1 秒一轮的 sleep 循环，检查 `abort` / `STOP_PATH` / `--nap-cancel`；
     每秒写 `state.json`（`phase="nap_wait"`、`wake_at`、`remain`），`--status` 直接读。
   - 阶段 2 到点：调现成的 `run_ramp(cfg, seconds=ramp*60, tick=…, hold=True, label="小睡渐亮")`，
     强制点亮、爬升、到顶保持全都复用，不再写第二套。
   - 阶段 3 保持：复用保持逻辑，上限取 `nap_hold_minutes`。
   - 日志统一写 `sunrise_alarm.log`（不再单开 `nap-alarm.log`）。
3. **main() 加 `--nap` / `--nap-ramp` / `--nap-max-level` / `--nap-cancel`**；
   `--status` 增读 nap 状态。
4. **桌面 bat**：`nap-alarm-preview.bat` 退役 → 新增 `小睡-20分钟.bat`（CRLF + GBK）。
5. **GUI**：加一行「小睡 [20] 分钟 ［开始］［取消］」，复用同一入口。

## 6. Phase B（等 Phase A 跑顺）

- **防睡眠更硬**：注册一次性计划任务 `SunriseAlarmNap`（StartBoundary=now+N 分钟，`WakeToRun`），
  进程死掉/机器睡了也能叫醒。跟现有 `task-definition.xml` 是同一套 XML，只换触发器。
- **再眯 5 分钟**：保持阶段日光层加按钮 + 热键 `Ctrl+Alt+S` → 亮度回落到 `nap_start_level` 重新计时。
  需要把 `run_ramp` 的保持循环改成可回调（`hold_decider()`）。
- **和鲸汐联动**：鱼片在聊天里说「我睡 20 分钟」→ 我用 WMI 起 detached `pythonw --nap 20`
  （必须 WMI，Start-Process 会被 exec 会话收掉，见 AGENTS.md）。

## 7. 验收清单

- `--nap 1 --nap-ramp 1`：亮 → 到 100 → 保持 → ESC 关，日志完整。
- `--nap 2 --nap-ramp 1` + 待机阶段 `--nap-cancel`：全程不亮，进程 5 秒内退出。
- `--nap 3 --nap-ramp 1`：到点前屏幕保持原状（不主动点亮）。
- 保持上限生效：`--nap 1 --nap-ramp 1` + `nap_hold_minutes=0.2` → 12 秒后自停。
- `--status` 在待机阶段显示剩余时间。

## 8. 待鱼片拍板

1. 睡着时屏幕亮不亮？默认 **不亮，最后 10 分钟才醒屏**。
2. 叫醒后保持多久？默认 **30 分钟封顶**。
3. 要不要「再眯 5 分钟」？（Phase B，会多改保持循环）
