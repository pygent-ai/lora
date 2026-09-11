# Lora Session CLI

## 单轮执行

```powershell
uv run lora session run --new --message "检查当前项目"
uv run lora --agent dev session run --new --message "运行测试并总结失败"
uv run lora session run <session_id> --message "继续处理"
```

命令返回稳定的 JSON 结果，包括 `session_id`、`case_run_id`、
`execution_id`、`status`、`final_answer` 和 `run_dir`。

## 交互模式

```powershell
uv run lora session chat --new
uv run lora session chat <session_id>
```

交互模式直接订阅 Pygent execution journal：

- `model.text.delta` 输出正文；
- `lora.approval.requested` 触发终端审批；
- execution 完成、失败或取消由 Pygent Runtime 管理。

模型通过用户级 `~/.lora/config.yaml` 中的
`agents[].model_request.routes` 配置，所有项目共用。CLI 不提供
`--model`，也不会读取旧的单模型字段。

`session run` 默认拒绝需要交互审批的高风险工具，除非工具名位于
`runtime.approvals.preauthorized_tools`。`session chat` 可以在终端中完成审批。

## 会话协作

```powershell
# 列出当前作用域的会话
uv run lora session list

# 新建会话并在独立 worker 中执行
uv run lora session start --source-session <current_session_id> --message "检查失败用例" --submission-id task-001

# 给正在工作的已有会话异步发送 Agent 消息
uv run lora session send <session_id> --source-session <current_session_id> --message "补充检查类型错误" --submission-id task-002

# 查询或等待后台 operation / Agent message
uv run lora session status <operation_id>
uv run lora session wait <operation_id>
uv run lora session status <message_id>
uv run lora session wait <message_id>
```

`start` 创建 Agent 会话并把首个 turn 持久化为 operation，随后由无交互
worker 接管执行。返回的 `operation_id` 可用于查询和等待；`wait` 也能在
原 worker 异常退出且租约过期后恢复该 operation。带 `--source-session` 时，
子会话进入任一终态都会向来源会话的收件箱原子写入一条完成消息；终态更新与
回传要么同时成功，要么同时回滚。没有来源会话的独立启动不会生成回传。

完成消息仍使用下文的 `<runtime-context>` 信封，其来源是子会话，内容为结构化
JSON。例如：

```json
{
  "type": "session.completed",
  "operation_id": "op-...",
  "session_id": "child-session-id",
  "case_id": "collaboration",
  "execution_id": "execution-...",
  "case_run_id": "run-...",
  "status": "passed",
  "final_answer": "检查结果",
  "error": null
}
```

父 Agent 正在工作时会在下一次工具结果后收到该消息；父会话空闲时消息会持久
保留至其下一轮的工具边界。因此 `start` 本身保持异步，无需用同步等待占住父
Agent。`submission_id` 和内部完成事件均为幂等写入。

`send` 不创建 turn，也不启动 worker。它只把消息持久化到目标会话的收件箱，
立即返回 `message_id`。目标 Agent 下一次完成工具调用时，runtime 会在对应
tool result 后附加如下低权限上下文，然后将消息标记为 `delivered`：

```xml
<runtime-context>
  <agent-message message-id="..." source-session-id="..." source-agent-alias="...">
    消息内容
  </agent-message>
</runtime-context>
```

如果目标 Agent 当前空闲，消息保持 `queued`，直到该会话后续某个 turn 到达
工具边界；`session wait <message_id>` 因而也会继续等待，不会主动唤醒会话。
`submission_id` 是调用方提供的幂等键，相同键不能用于不同请求。

协作命令始终输出 JSON。`session chat` 是唯一面向人类 stdin 的交互入口，
自动化调用不应使用它。

CLI、HTTP API 和桌面端通过同一个 `SessionTurnService` 提交 turn；协作
命令在其上复用 `SessionCollaborationService`；Agent message 由共享的
`SessionCollaborationStore` 投递，CLI 不直接创建或关闭 `LoraRuntimeService`。

配置了 `delegation.allowed_agents` 后，模型会获得 `agent_start`、
`agent_send`、`agent_status`、`agent_list` 和 `agent_wait` 五个原生工具。
`agent_list` 返回当前会话参与的最近协作任务；`agent_wait` 既可等待单个 ID，
也可传入多个 ID 并在任意任务完成时返回。它们直接复用
`SessionCollaborationService`，与上述 CLI 命令读写同一 operation 和消息队列；
旧的 `delegate`、`delegate_background` 工具不存在。
