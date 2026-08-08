# Lora Chat CLI

## 运行

```powershell
uv run lora chat --message "检查当前项目"
uv run lora --agent dev chat --message "运行测试并总结失败"
uv run lora chat --new
uv run lora chat --session <session_id>
```

模型只能通过 `agents[].model_request.routes` 配置。CLI 不再提供 `--model`，也不会读取旧的单模型字段。

交互模式直接订阅 Pygent execution journal：

- `model.text.delta` 输出正文；
- `model.reasoning.delta` 输出推理片段；
- `lora.approval.requested` 触发终端审批；
- execution 完成、失败或取消由 Pygent Runtime 管理。

非交互模式默认拒绝需要审批的高风险工具，除非工具名位于 `runtime.approvals.preauthorized_tools`。

单轮命令返回 Lora 业务结果及 `runtime_execution_id`。执行事件保存在 `runtime.durability.history_path` 指向的 SQLite journal 中。
