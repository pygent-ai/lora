# `lora automation`

`lora automation` manages durable local scheduled tasks. In a source checkout,
invoke it as `uv run lora automation`. The desktop/API process
executes queued work; CLI commands read and write the shared
`~/.lora/automations-v1.sqlite3` database.

Create a standalone one-time task:

```powershell
lora --workspace-root E:\Projects\example automation create `
  --name "检查构建" `
  --prompt "运行测试并总结失败原因" `
  --at "2026-09-12T09:00:00+08:00" `
  --timezone Asia/Shanghai `
  --standalone
```

Create a heartbeat task in an existing session:

```powershell
lora --workspace-root E:\Projects\example automation create `
  --name "继续检查" `
  --prompt-file task.txt `
  --rrule "FREQ=DAILY;BYHOUR=9;BYMINUTE=0" `
  --timezone Asia/Shanghai `
  --session SESSION_ID
```

Available operations are `create`, `list`, `show`, `update`, `pause`, `resume`,
`delete`, `run`, and `runs`. Commands emit JSON. `--prompt-file -` reads the
instruction from stdin. Use exactly one of `--at` and `--rrule`, and exactly one
of `--standalone` and `--session` when creating a task.

Scheduled execution requires Lora Desktop or `lora-api` to be running. A periodic
task missed while Lora was stopped is caught up once at the next startup.
