/* 日出光闹钟 · 网页版前端逻辑
 * 只通过 window.pywebview.api.* 与 Python 壳通信；不发任何外网请求。
 */
"use strict";

const WEEKDAYS = [["MON","周一"],["TUE","周二"],["WED","周三"],["THU","周四"],
                  ["FRI","周五"],["SAT","周六"],["SUN","周日"]];
let api = null;
let pollTimer = null;

const $ = (id) => document.getElementById(id);

function logLine(text) {
  const el = $("log");
  const t = new Date().toLocaleTimeString("zh-CN", {hour12:false});
  el.textContent += `[${t}] ${text}\n`;
  el.scrollTop = el.scrollHeight;
}

function toast(msg, ms = 3200) {
  let el = document.querySelector(".toast");
  if (el) el.remove();
  el = document.createElement("div");
  el.className = "toast";
  el.textContent = msg;
  document.body.appendChild(el);
  setTimeout(() => el.remove(), ms);
}

function buildDays() {
  const box = $("days");
  box.innerHTML = "";
  for (const [code, name] of WEEKDAYS) {
    const lab = document.createElement("label");
    lab.innerHTML = `<input type="checkbox" data-day="${code}"> ${name}`;
    box.appendChild(lab);
  }
}

function fillForm(cfg) {
  $("start_time").value = cfg.start_time ?? "";
  $("end_time").value = cfg.end_time ?? "";
  $("start_level").value = cfg.start_level ?? 5;
  $("max_level").value = cfg.max_level ?? 100;
  $("curve").value = cfg.curve ?? 1.0;
  $("tick_seconds").value = cfg.tick_seconds ?? 5;
  $("overlay_mode").value = cfg.overlay_mode ?? "sunrise";
  $("monitor_index").value = cfg.monitor_index ?? 0;
  $("hold_max_minutes").value = cfg.hold_max_minutes ?? 0;
  $("task_time").value = cfg.task_time ?? cfg.start_time ?? "07:00";
  $("overlay").checked = cfg.overlay !== false;
  $("hold").checked = cfg.hold !== false;
  const days = cfg.task_weekdays || [];
  document.querySelectorAll('#days input[type=checkbox]').forEach((c) => {
    c.checked = days.includes(c.dataset.day);
  });
  const cp = cfg.curve_points;
  $("curve_points").value = (cp && (Array.isArray(cp) ? cp.length : true)) ? JSON.stringify(cp) : "";
}

function collectForm() {
  const days = [];
  document.querySelectorAll('#days input[type=checkbox]').forEach((c) => {
    if (c.checked) days.push(c.dataset.day);
  });
  return {
    start_time: $("start_time").value.trim(),
    end_time: $("end_time").value.trim(),
    task_time: $("task_time").value.trim() || $("start_time").value.trim(),
    start_level: Number($("start_level").value),
    max_level: Number($("max_level").value),
    curve: Number($("curve").value),
    tick_seconds: Number($("tick_seconds").value),
    overlay_mode: $("overlay_mode").value,
    monitor_index: Number($("monitor_index").value),
    hold_max_minutes: Number($("hold_max_minutes").value),
    overlay: $("overlay").checked,
    hold: $("hold").checked,
    task_weekdays: days,
    curve_points: $("curve_points").value.trim(),
  };
}

async function doReload(silent = false) {
  try {
    const cfg = await api.load_config();
    fillForm(cfg);
    if (!silent) { logLine("已从 config.json 重新载入。"); toast("已载入 config.json"); }
  } catch (e) {
    logLine("载入失败：" + e);
  }
}

async function doSave() {
  try {
    const res = await api.save_config(collectForm());
    if (res && res.ok) {
      fillForm(res.config);
      logLine("已保存 config.json：" + JSON.stringify({
        start_time: res.config.start_time, end_time: res.config.end_time,
        start_level: res.config.start_level, max_level: res.config.max_level,
      }));
      toast("已保存到 config.json ✓");
    } else {
      logLine("保存失败：" + (res && res.error));
      toast("保存失败：" + (res && res.error));
    }
  } catch (e) {
    logLine("保存异常：" + e);
    toast("保存异常：" + e);
  }
}

async function maybeSave() {
  // 触发动作前先把当前表单存盘（和 tkinter 版一致：点按钮即保存）
  const res = await api.save_config(collectForm());
  if (!res || !res.ok) {
    toast("参数不对，未执行：" + (res && res.error));
    logLine("参数不对，未执行：" + (res && res.error));
    return false;
  }
  fillForm(res.config);
  return true;
}

async function renderLive() {
  try {
    const snap = await api.snapshot();
    if (snap.brightness !== null && snap.brightness !== undefined) {
      $("bnum").textContent = snap.brightness;
    } else {
      $("bnum").textContent = "--";
    }
    const st = snap.state;
    if (st && st.phase === "hold") {
      $("stateText").textContent = `保持中 · 亮度 ${st.level} · 已保持 ${st.held}s（ESC/点一下/Ctrl+Alt+Q 关闭）`;
    } else if (st) {
      $("stateText").textContent = `爬升中 · 亮度 ${st.level} · ${st.elapsed}s / ${st.total_seconds}s`;
    } else {
      $("stateText").textContent = snap.bright_err
        ? ("空闲 · " + snap.bright_err)
        : "空闲（未在爬升）";
    }
  } catch (e) {
    // 静默，避免刷屏
  }
}

let pollStop = false;
async function pollLoop() {
  while (!pollStop) {
    if ($("autoPoll").checked) await renderLive();
    await new Promise((r) => setTimeout(r, 3000));
  }
}
function startPoll() {
  pollStop = false;
  pollLoop();
}

function hook() {
  $("btnSave").onclick = doSave;
  $("btnReload").onclick = () => doReload(false);
  $("btnClearLog").onclick = () => { $("log").textContent = ""; };

  $("btnPreview").onclick = async () => {
    if (!(await maybeSave())) return;
    const secs = Math.max(1, Number($("previewSecs").value) || 10);
    // 预览为避免占满整屏：若日光层样式=off 或用户不想铺，这里按 overlay_mode 交给 CLI；
    // 但为了安全（别挂一晚）始终 --no-hold。
    const res = await api.preview(secs, $("overlay").checked, false);
    logLine("触发预览 " + secs + "s：" + (res.ok ? res.cmd : res.error));
    toast(res.ok ? `预览 ${secs}s 已启动` : ("预览失败：" + res.error));
    setTimeout(renderLive, 800);
  };

  $("btnStart").onclick = async () => {
    if (!(await maybeSave())) return;
    const res = await api.start($("overlay").checked, $("hold").checked);
    logLine("触发立即开始：" + (res.ok ? res.cmd : res.error));
    toast(res.ok ? "已开始（按 config.json）" : ("开始失败：" + res.error));
  };

  $("btnStop").onclick = async () => {
    const res = await api.stop();
    logLine("停止：" + (res.output || ""));
    toast("已发送停止指令");
    setTimeout(renderLive, 800);
  };

  $("btnWake").onclick = async () => {
    logLine("正在测试强制点亮屏幕…");
    const res = await api.wake();
    logLine(res.output || "");
    toast(res.output || "");
    renderLive();
  };

  $("btnRead").onclick = async () => {
    const snap = await api.refresh();
    renderLive();
    logLine("当前亮度：" + (snap.brightness ?? snap.bright_err ?? "?"));
  };

  $("btnList").onclick = async () => {
    const res = await api.list_monitors();
    logLine("显示器列表：\n" + (res.output || ""));
    toast("已在日志里列出显示器");
  };

  $("btnInstall").onclick = async () => {
    if (!(await maybeSave())) return;
    if (!confirm("安装/更新计划任务（需要管理员权限）。继续？")) return;
    const res = await api.install_task();
    logLine("安装任务：" + (res.output || ""));
    toast(res.ok ? "计划任务已安装/更新" : "安装失败（多半要管理员）");
  };

  $("btnRemove").onclick = async () => {
    if (!confirm("删除计划任务 SunriseAlarm？")) return;
    const res = await api.remove_task();
    logLine("删除任务：" + (res.output || ""));
    toast(res.ok ? "计划任务已删除" : (res.output || "删除失败"));
  };

  $("btnTaskQuery").onclick = async () => {
    const res = await api.task_query();
    logLine("计划任务状态：\n" + (res.output || ""));
    toast("任务状态已写入日志");
  };

  $("btnSkip").onclick = async () => {
    const res = await api.skip_next();
    logLine("跳过下次：" + (res.output || ""));
    toast(res.ok ? "已设置：下次不响" : "设置失败");
  };
}

window.addEventListener("pywebviewready", async () => {
  api = window.pywebview.api;
  buildDays();
  hook();
  await doReload(true);
  renderLive();
  startPoll();
  logLine("界面就绪。背光动作都走 sunrise_alarm.py CLI。");
});
