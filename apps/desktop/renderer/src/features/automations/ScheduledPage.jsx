import React, { useEffect, useMemo, useState } from "react";
import { CalendarClock, Pause, Play, Plus, RefreshCw, Trash2, X } from "lucide-react";

const EMPTY = {
  name: "",
  prompt: "",
  workspace_root: "",
  destination: "standalone",
  target_session_id: "",
  schedule_type: "daily",
  at_time: "",
  time: "09:00",
  weekday: "MO",
  rrule: "",
  timezone: Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC",
};

export function ScheduledPage({ api, projects, activeSession, settings, onOpenSession }) {
  const [items, setItems] = useState([]);
  const [runs, setRuns] = useState({});
  const [status, setStatus] = useState("");
  const [editing, setEditing] = useState(null);
  const [form, setForm] = useState({ ...EMPTY, workspace_root: settings.workspace_root || "" });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function refresh() {
    const result = await api.listAutomations(status);
    setItems(result.automations || []);
  }

  useEffect(() => { refresh().catch((err) => setError(String(err.message || err))); }, [status]);

  const projectOptions = useMemo(() => {
    const values = [...projects];
    if (settings.workspace_root && !values.some((item) => item.workspace_root === settings.workspace_root)) {
      values.unshift({ workspace_root: settings.workspace_root, label: settings.workspace_root });
    }
    return values;
  }, [projects, settings.workspace_root]);

  function startCreate() {
    setEditing("new");
    setForm({ ...EMPTY, workspace_root: settings.workspace_root || projectOptions[0]?.workspace_root || "" });
  }

  function startEdit(item) {
    setEditing(item.automation_id);
    setForm({
      ...EMPTY,
      ...item,
      schedule_type: item.at_time ? "once" : "custom",
      target_session_id: item.target_session_id || "",
      rrule: item.rrule || "",
      at_time: item.at_time ? localInputValue(item.at_time) : "",
    });
  }

  async function save(event) {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      const payload = schedulePayload(form);
      if (editing === "new") await api.createAutomation(payload);
      else await api.updateAutomation(editing, payload);
      setEditing(null);
      await refresh();
    } catch (err) {
      setError(String(err.message || err));
    } finally {
      setBusy(false);
    }
  }

  async function action(operation) {
    setBusy(true);
    setError("");
    try { await operation(); await refresh(); }
    catch (err) { setError(String(err.message || err)); }
    finally { setBusy(false); }
  }

  async function toggleRuns(item) {
    if (runs[item.automation_id]) {
      setRuns((current) => ({ ...current, [item.automation_id]: null }));
      return;
    }
    try {
      const result = await api.listAutomationRuns(item.automation_id);
      setRuns((current) => ({ ...current, [item.automation_id]: result.runs || [] }));
    } catch (err) { setError(String(err.message || err)); }
  }

  return <section className="scheduled-page" aria-label="定时任务">
    <header className="scheduled-header">
      <div><span className="scheduled-eyebrow">AUTOMATIONS</span><h2>定时任务</h2><p>让 Lora 在后台按计划执行项目任务。</p></div>
      <button className="send scheduled-create" type="button" onClick={startCreate}><Plus size={16} />新建</button>
    </header>
    <div className="scheduled-filters">
      {[['', '全部'], ['active', '运行中'], ['paused', '已暂停'], ['completed', '已完成']].map(([value, label]) =>
        <button className={status === value ? "active" : ""} type="button" key={value} onClick={() => setStatus(value)}>{label}</button>)}
      <button type="button" title="刷新" onClick={() => refresh()}><RefreshCw size={14} /></button>
    </div>
    {error && <div className="scheduled-error" role="alert">{error}</div>}
    <div className="scheduled-list">
      {!items.length && <div className="scheduled-empty"><CalendarClock size={32} /><strong>还没有定时任务</strong><span>新建一个任务，或在对话中让 Lora 使用 automation CLI 创建。</span></div>}
      {items.map((item) => <article className="automation-card" key={item.automation_id}>
        <div className="automation-card-main" onClick={() => startEdit(item)}>
          <div className="automation-icon"><CalendarClock size={17} /></div>
          <div><h3>{item.name}</h3><p>{item.prompt}</p><div className="automation-meta"><span>{scheduleLabel(item)}</span><span>{item.destination === "heartbeat" ? "返回原会话" : "独立会话"}</span><span>{shortPath(item.workspace_root)}</span></div></div>
          <span className={`automation-status ${item.status}`}>{statusLabel(item.status)}</span>
        </div>
        <div className="automation-actions">
          <button type="button" disabled={busy} onClick={() => action(() => api.runAutomation(item.automation_id))}><Play size={14} />立即运行</button>
          {item.status === "active" ? <button type="button" disabled={busy} onClick={() => action(() => api.pauseAutomation(item.automation_id))}><Pause size={14} />暂停</button> :
            item.status === "paused" && <button type="button" disabled={busy} onClick={() => action(() => api.resumeAutomation(item.automation_id))}><Play size={14} />恢复</button>}
          <button type="button" onClick={() => toggleRuns(item)}>运行记录</button>
          <button className="danger" type="button" disabled={busy} onClick={() => action(() => api.deleteAutomation(item.automation_id))}><Trash2 size={14} /></button>
        </div>
        {runs[item.automation_id] && <div className="automation-runs">{runs[item.automation_id].length === 0 ? <span>暂无运行记录</span> : runs[item.automation_id].map((run) =>
          <button type="button" key={run.run_id} disabled={!run.session_id} onClick={() => run.session_id && onOpenSession(run.session_id, item.workspace_root)}>
            <span className={`run-dot ${run.status}`} /><strong>{run.status}</strong><span>{formatDate(run.scheduled_for)}</span><span>{run.error || run.final_answer || ""}</span>
          </button>)}</div>}
      </article>)}
    </div>
    {editing && <div className="automation-editor-backdrop"><form className="automation-editor" onSubmit={save}>
      <header><div><span className="scheduled-eyebrow">{editing === "new" ? "NEW AUTOMATION" : "EDIT AUTOMATION"}</span><h3>{editing === "new" ? "新建定时任务" : "编辑定时任务"}</h3></div><button type="button" onClick={() => setEditing(null)}><X /></button></header>
      <label>名称<input required value={form.name} onChange={(event) => setForm({ ...form, name: event.target.value })} /></label>
      <label>任务提示词<textarea required rows="6" value={form.prompt} onChange={(event) => setForm({ ...form, prompt: event.target.value })} /></label>
      <label>项目<select required value={form.workspace_root} onChange={(event) => setForm({ ...form, workspace_root: event.target.value })}>{projectOptions.map((project) => <option value={project.workspace_root} key={project.workspace_root}>{project.label || project.workspace_root}</option>)}</select></label>
      <div className="automation-form-row"><label>运行方式<select value={form.destination} onChange={(event) => setForm({ ...form, destination: event.target.value, target_session_id: event.target.value === "heartbeat" ? activeSession?.session_id || "" : "" })}><option value="standalone">每次创建新会话</option><option value="heartbeat">返回当前会话</option></select></label><label>时区<input required value={form.timezone} onChange={(event) => setForm({ ...form, timezone: event.target.value })} /></label></div>
      {form.destination === "heartbeat" && <label>目标会话<input required value={form.target_session_id} onChange={(event) => setForm({ ...form, target_session_id: event.target.value })} /></label>}
      <label>频率<select value={form.schedule_type} onChange={(event) => setForm({ ...form, schedule_type: event.target.value })}><option value="once">单次</option><option value="hourly">每小时</option><option value="daily">每天</option><option value="weekly">每周</option><option value="custom">自定义 RRULE</option></select></label>
      {form.schedule_type === "once" ? <label>执行时间<input required type="datetime-local" value={form.at_time} onChange={(event) => setForm({ ...form, at_time: event.target.value })} /></label> : form.schedule_type === "custom" ? <label>RRULE<input required placeholder="FREQ=DAILY;BYHOUR=9;BYMINUTE=0" value={form.rrule} onChange={(event) => setForm({ ...form, rrule: event.target.value })} /></label> : <div className="automation-form-row">{form.schedule_type === "weekly" && <label>星期<select value={form.weekday} onChange={(event) => setForm({ ...form, weekday: event.target.value })}>{[['MO','周一'],['TU','周二'],['WE','周三'],['TH','周四'],['FR','周五'],['SA','周六'],['SU','周日']].map(([value,label]) => <option value={value} key={value}>{label}</option>)}</select></label>} {form.schedule_type !== "hourly" && <label>时间<input type="time" required value={form.time} onChange={(event) => setForm({ ...form, time: event.target.value })} /></label>}</div>}
      <footer><button type="button" onClick={() => setEditing(null)}>取消</button><button className="send" disabled={busy} type="submit">保存</button></footer>
    </form></div>}
  </section>;
}

function schedulePayload(form) {
  const payload = { name: form.name.trim(), prompt: form.prompt.trim(), workspace_root: form.workspace_root, destination: form.destination, target_session_id: form.destination === "heartbeat" ? form.target_session_id : null, timezone: form.timezone };
  if (form.schedule_type === "once") payload.at_time = form.at_time;
  else if (form.schedule_type === "custom") payload.rrule = form.rrule;
  else if (form.schedule_type === "hourly") payload.rrule = "FREQ=HOURLY";
  else {
    const [hour, minute] = form.time.split(":").map(Number);
    payload.rrule = `FREQ=${form.schedule_type === "weekly" ? "WEEKLY" : "DAILY"};${form.schedule_type === "weekly" ? `BYDAY=${form.weekday};` : ""}BYHOUR=${hour};BYMINUTE=${minute};BYSECOND=0`;
  }
  return payload;
}

function localInputValue(value) { const date = new Date(value); const local = new Date(date.getTime() - date.getTimezoneOffset() * 60000); return local.toISOString().slice(0, 16); }
function scheduleLabel(item) { return item.at_time ? `单次 · ${formatDate(item.at_time)}` : `${item.rrule} · 下次 ${item.next_run_at ? formatDate(item.next_run_at) : "—"}`; }
function formatDate(value) { return value ? new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" }).format(new Date(value)) : "—"; }
function statusLabel(value) { return ({ active: "运行中", paused: "已暂停", completed: "已完成" })[value] || value; }
function shortPath(value) { const text = String(value || ""); return text.length > 46 ? `…${text.slice(-43)}` : text; }
