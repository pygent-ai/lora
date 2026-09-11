# Agent framework resource benchmark

The benchmark separates lightweight agent loops, durable execution runtimes, and service/deployment runtimes. The durable lane enables official persistence for Pygent, LangGraph, Mastra, OpenAI Agents + DBOS, and Vercel WorkflowAgent. AgentScope Runtime, Agno AgentOS, and Hayhooks are reported in a separate service lane because HTTP/session/deployment management is not the same guarantee as replayable step execution. Runtime-enabled and runtime-disabled modes are never mixed into one overall rank.

![Agent 框架分赛道公平排名与测试方法](assets/agent-framework-ranking-2026-08-13.svg)

## Method

- Windows host; local OpenAI-compatible mock excluded from framework resource totals.
- The mock requests three tool calls and one final answer. Every newly measured runtime adapter requires the executor to run steps 1, 2 and 3 exactly once per request; final text alone cannot pass.
- Baseline scenarios use three fresh-process repetitions. Refreshed short-workload Pygent scenarios and refreshed high-load top-ten entries use ten; corrected Pygent high-load rows use three; tables show medians.
- Short scenarios are cold (one request), serial (eight requests at concurrency one), and concurrent-8.
- High load submits 200 requests at concurrency 200: 800 model HTTP round trips and 600 tool executions per repetition.
- CPU is cumulative process-tree CPU time, not CPU percentage. RSS is peak process-tree resident memory.
- High-load qualification requires every request to succeed in every accepted repetition, exact tool-count validation and no resource-guard termination.
- The 4 GB RSS guard applies to high load. Each service request has a 180-second client deadline; the process-level capacity confirmation has a 300-600 second bound.
- A mode is called durable only when the framework's persistence backend is active. A class or API named `workflow` running as an ordinary in-process call is not sufficient.

## Short workload

| Framework / mode | Cold RSS MB | Cold CPU s | 8-conc RSS MB | 8-conc CPU s | 8-conc run s | req/s | speedup vs serial | write MB |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| vercel-ai-sdk | 65.7 | 0.219 | 74.8 | 0.297 | 0.091 | 87.97 | 2.06x | 0.001 |
| pi-agent-core | 74.4 | 0.547 | 82.9 | 0.688 | 0.198 | 40.31 | 1.61x | 0.000 |
| pygent-direct | 65.7 | 0.633 | 67.3 | 0.781 | 0.062 | 130.07 | 2.37x | 0.002 |
| pygent-runtime-disabled | 66.4 | 0.688 | 68.2 | 0.789 | 0.073 | 109.99 | 2.30x | 0.002 |
| pygent-runtime-preferred | 67.9 | 0.742 | 70.6 | 0.852 | 0.163 | 49.05 | 2.87x | 2.866 |
| mastra | 181.6 | 1.000 | 192.4 | 1.094 | 0.232 | 34.42 | 1.52x | 0.001 |
| openai-sdk | 105.4 | 2.297 | 108.1 | 1.953 | 0.602 | 13.28 | 1.33x | 0.005 |
| microsoft-agent-framework | 107.8 | 1.781 | 109.9 | 2.125 | 0.648 | 12.34 | 1.32x | 0.004 |
| agno | 118.9 | 2.141 | 121.2 | 2.156 | 0.652 | 12.27 | 1.42x | 0.004 |
| agentscope | 134.8 | 2.391 | 137.2 | 2.359 | 0.539 | 14.83 | 1.34x | 0.004 |
| haystack | 135.6 | 2.125 | 137.8 | 2.375 | 0.674 | 11.87 | 1.20x | 0.004 |
| langgraph | 136.3 | 2.391 | 138.6 | 2.453 | 0.187 | 42.67 | 1.93x | 0.005 |
| smolagents | 119.4 | 2.203 | 121.2 | 2.609 | 1.077 | 7.43 | 1.02x | 0.018 |
| openai-agents | 145.1 | 3.344 | 146.9 | 2.953 | 0.694 | 11.53 | 1.15x | 0.005 |
| pydantic-ai | 149.3 | 3.344 | 151.8 | 3.734 | 1.020 | 7.84 | 0.93x | 0.005 |
| qwen-agent | 192.3 | 2.375 | 251.4 | 4.375 | 0.951 | 8.41 | 3.31x | 0.007 |
| langchain | 140.4 | 4.047 | 141.4 | 4.812 | 0.533 | 15.01 | 1.71x | 0.005 |
| strands | 127.7 | 2.234 | 168.9 | 5.016 | 2.815 | 2.84 | 0.82x | 0.002 |
| openjiuwen | 254.9 | 5.609 | 257.6 | 5.719 | 0.963 | 8.31 | 1.14x | 0.335 |
| google-adk | 371.3 | 7.312 | 376.6 | 7.453 | 7.022 | 1.14 | 0.90x | 0.005 |
| openhands | 271.3 | 6.625 | 272.2 | 7.469 | 1.661 | 4.82 | 1.08x | 0.017 |
| llamaindex | 266.2 | 5.844 | 578.5 | 24.688 | 24.639 | 0.32 | 0.91x | 0.005 |

In the mixed-date table, Pygent direct has the lowest short-workload RSS and the fastest concurrent-8 run. Vercel AI SDK uses the least CPU. Runtime disabled adds 0.9 MB RSS, 0.008 CPU-s and 0.011 seconds over Pygent direct at concurrency eight. Preferred durability adds 3.3 MB RSS, 0.070 CPU-s, 0.102 seconds and 2.865 MB of writes over direct execution. All three Pygent modes passed the executor-count check in every repetition.

## Agent-loop lane: 200 concurrent agents

This lane ranks only the non-durable agent-loop adapters. The score gives equal weight to run time, P95 latency, CPU, RSS and disk writes. It is an overhead comparison, not a runtime-capability comparison. Scores below were recalculated across the 19 comparable entries from the displayed baseline metrics; Pygent runtime disabled/preferred are deliberately excluded.

| Rank | Framework | Run s | req/s | P95 s | CPU s | RSS MB | Write MB | Score |
|---:|---|---:|---:|---:|---:|---:|---:|---:|
| 1 | vercel-ai-sdk | 0.760 | 263.3 | 0.735 | 1.328 | 164.5 | 0.026 | 92.2 |
| 2 | pi-agent-core | 1.162 | 172.1 | 1.157 | 2.070 | 154.1 | 0.026 | 91.1 |
| 3 | pygent-direct | 1.234 | 162.1 | 1.213 | 2.125 | 94.1 | 0.029 | 90.0 |
| 4 | mastra | 2.063 | 96.9 | 2.045 | 4.023 | 406.4 | 0.026 | 72.2 |
| 5 | agentscope | 4.427 | 45.2 | 3.807 | 6.516 | 171.8 | 0.033 | 66.7 |
| 6 | openai-sdk | 4.479 | 44.7 | 3.823 | 5.883 | 148.0 | 0.036 | 61.7 |
| 7 | openai-agents | 3.952 | 50.6 | 3.911 | 6.531 | 171.0 | 0.035 | 60.6 |
| 8 | langgraph | 3.772 | 53.0 | 3.736 | 7.031 | 175.7 | 0.035 | 57.2 |
| 9 | agno | 5.118 | 39.1 | 4.182 | 7.094 | 158.8 | 0.034 | 52.8 |
| 10 | haystack | 5.161 | 38.8 | 4.273 | 6.812 | 173.6 | 0.034 | 50.0 |
| 11 | microsoft-agent-framework | 5.076 | 39.4 | 4.322 | 6.945 | 148.8 | 0.036 | 49.4 |
| 12 | pydantic-ai | 4.465 | 44.8 | 4.394 | 6.812 | 192.2 | 0.035 | 45.6 |
| 13 | langchain | 4.440 | 45.0 | 4.406 | 8.062 | 178.0 | 0.035 | 41.7 |
| 14 | smolagents | 6.187 | 32.3 | 4.565 | 8.562 | 129.1 | 0.377 | 37.8 |
| 15 | google-adk | 9.849 | 20.3 | 9.838 | 10.859 | 492.3 | 0.030 | 30.0 |
| 16 | openjiuwen | 10.723 | 18.7 | 9.778 | 13.844 | 297.1 | 8.222 | 16.7 |
| 17 | strands | 35.355 | 5.7 | 34.257 | 36.531 | 859.9 | 0.031 | 15.6 |
| 18 | openhands | 31.226 | 6.4 | 27.643 | 34.594 | 338.1 | 0.409 | 10.0 |
| 19 | qwen-agent | 13.375 | 15.0 | 11.427 | 70.734 | 790.5 | 0.104 | 8.9 |

Vercel AI SDK leads the loop-only lane, Pi Agent Core is second and Pygent direct is third. Pygent direct still has the lowest RSS. This table must not be used to conclude that Vercel or Pi provides a cheaper durable runtime: neither durable path is active here.

## Durable execution-runtime lane

This is the closest like-for-like runtime comparison. Each qualifying adapter uses a local SQLite-family backend, three fresh-process repetitions and exact tool-executor validation. Pygent uses `DurabilityPolicy.PREFERRED`, LangGraph uses `AsyncSqliteSaver`, Mastra uses `durable: true` with `LibSQLStore`, and OpenAI Agents runs inside DBOS workflows and steps. The persistence semantics are still not identical: Pygent journals executions, LangGraph and Mastra persist checkpoints/snapshots, and DBOS records workflow/step state.

### 200 concurrent agents

| Rank | Framework / official mode | Run s | req/s | P95 s | CPU s | RSS MB | Write MB | Threads | Score |
|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|
| 1 | Pygent 0.3.10 preferred | 3.828 | 52.2 | 3.758 | 4.906 | 102.2 | 95.958 | 23 | 98.6 |
| 2 | LangGraph 1.2.10 + SQLite | 10.844 | 18.4 | 10.814 | 11.922 | 169.3 | 55.074 | 37 | 85.5 |
| 3 | OpenAI Agents 0.22.0 + DBOS 2.31.0 | 38.844 | 5.1 | 38.059 | 42.062 | 220.0 | 68.876 | 639 | 35.0 |
| 4 | Mastra 1.64.0 durable + LibSQL | 22.699 | 8.8 | 22.453 | 23.266 | 628.1 | 621.132 | 32 | 28.4 |

Pygent wins wall time, P95, CPU and RSS. LangGraph writes 42.6% less data than Pygent. DBOS uses far less memory and disk than Mastra but creates 639 peak threads at 200 concurrency. Mastra is faster than DBOS here, yet its memory and write amplification lower its equal-weight resource score.

Vercel WorkflowAgent 1.0.70 on Workflow Local World does not qualify. Two runs completed 200/200 with a 147.292-second median, 262.672 CPU-s and roughly 3.89 GB RSS; the third crossed the 4 GB RSS guard. The production server itself starts in about 0.5 seconds, so the failure is workload memory growth, not the earlier dev-server startup race.

### 1,000 concurrent agents

| Rank | Framework / official mode | Run s | req/s | P95 s | CPU s | RSS MB | Write MB | Threads | Score |
|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|
| 1 | Pygent 0.3.10 preferred | 23.070 | 43.3 | 22.603 | 23.453 | 211.1 | 718.838 | 23 | 80.0 |
| 2 | LangGraph 1.2.10 + SQLite | 50.540 | 19.8 | 50.288 | 42.188 | 264.3 | 279.974 | 33 | 69.9 |
| 3 | OpenAI Agents 0.22.0 + DBOS 2.31.0 | 86.767 | 11.5 | 84.909 | 84.719 | 374.5 | 352.741 | 2434 | 16.7 |

Mastra does not qualify at 1,000 concurrency. All three processes exited with Node code 13 (`unsettled top-level await`) after 65-73 seconds, before producing an adapter result. Vercel WorkflowAgent also does not qualify: its capacity-confirmation run crossed the 4 GB RSS guard before producing a result. Pygent again wins four metrics; LangGraph writes 61.1% less. DBOS completes reliably but reaches 2,434 peak threads, which is a serious deployment concern despite moderate RSS.

## Service/deployment-runtime lane

This lane includes the service child process, HTTP transport, runtime routing/lifecycle, agent loop and configured persistence. It is intentionally not scored against durable step runtimes. Agno uses AgentOS, SQLite, stored events and the currently implemented `tool-batch` checkpoint. AgentScope uses AgentApp and its runtime lifecycle. Hayhooks deploys a real Haystack Agent pipeline and calls its generated REST endpoint.

| Load | Result | Run s | P95 s | CPU s | RSS MB | Write MB | Threads |
|---|---|---:|---:|---:|---:|---:|---:|
| 200 | Agno AgentOS 3.0.6: 200/200 × 3 | 11.041 | 10.794 | 14.953 | 254.3 | 37.742 | 40 |
| 200 | AgentScope Runtime 1.1.6.post2: 0/200 × 3 | 182.953 | 182.824 | 13.031 | 228.7 | 0.851 | 39 |
| 200 | Hayhooks 1.24.0: 0/200 × 3 | 181.896 | 181.808 | 10.766 | 206.5 | 0.857 | 53 |
| 1,000 | Agno AgentOS 3.0.6: 1000/1000 × 3 | 60.788 | 48.657 | 81.359 | 285.0 | 189.232 | 40 |
| 1,000 | AgentScope Runtime 1.1.6.post2: 0/1000 × 1 | 195.719 | 195.646 | 36.422 | 238.6 | 4.221 | 38 |
| 1,000 | Hayhooks 1.24.0: 0/1000 × 1 | 197.180 | 197.097 | 34.984 | 220.5 | 4.236 | 55 |

Agno is the only service runtime here that qualifies at both loads. AgentScope and Hayhooks hit the 180-second per-request deadline even at 200 concurrency; their relatively low CPU and write totals show that the dominant symptom is waiting/throughput collapse rather than CPU saturation. Agno 3.0.6 exposes `checkpoint="tools"` in its public type but raises `NotImplementedError` at startup; the benchmark therefore uses its documented working alternative, `tool-batch`.

### Runtime activation audit

| Framework | Official durable capability | Benchmark treatment |
|---|---|---|
| Pygent | Embedded Runtime journal and admission | Enabled and ranked |
| LangGraph | Checkpointer; SQLite implementation | Enabled and ranked |
| Mastra | Durable Agent plus workflow snapshots; LibSQL storage | Enabled; ranked at 200, failed qualification at 1,000 |
| Vercel AI SDK | `WorkflowAgent` on Workflow DevKit | Local World enabled and all server processes counted; failed the RSS qualification rule |
| OpenAI Agents SDK | Durable integrations with Temporal, DBOS, Restate and Dapr | DBOS integration enabled, SQLite-backed, all processes counted and ranked |
| Pydantic AI | Durable integrations with Temporal, DBOS, Prefect and Restate | External engine required; rank only after engine and worker processes are included |
| Microsoft Agent Framework | Workflow checkpoints and Durable Task integration | Not a drop-in Agent runner switch; needs a semantically equivalent workflow adapter |
| AgentScope | AgentApp service runtime | Enabled in the separate service lane; failed the request deadline |
| Agno | AgentOS runtime/control plane and storage | Enabled with SQLite events and `tool-batch` checkpoints in the service lane |
| Haystack | Hayhooks REST deployment runtime | Enabled with a deployed Agent pipeline in the service lane; failed the request deadline |
| Pi Agent Core | Stateful in-process agent loop | Loop lane only |

Relevant official documentation: [LangGraph durable execution](https://docs.langchain.com/oss/python/langgraph/durable-execution), [Mastra durable agents](https://mastra.ai/docs/agents/durable-agents), [Vercel WorkflowAgent](https://vercel.com/kb/guide/what-is-workflowagent), [OpenAI Agents durable integrations](https://openai.github.io/openai-agents-python/running_agents/), [AgentScope Runtime](https://runtime.agentscope.io/en/concept.html), [Agno AgentOS](https://docs.agno.com/agent-os/overview), [Hayhooks](https://docs.haystack.deepset.ai/docs/hayhooks), [Pydantic AI durable execution](https://ai.pydantic.dev/durable_execution/), and [Microsoft workflow checkpoints](https://learn.microsoft.com/en-us/agent-framework/workflows/checkpoints).

## Agent-loop lane: 1,000 concurrent agents

The 2026-09-08 extreme-load run retests the current top entries with three fresh processes each. Every repetition submits 1,000 requests concurrently, producing 4,000 model HTTP round trips and 3,000 tool executions. Scores are recalculated across the seven qualifying non-durable entries, so they are not directly comparable with the 19-entry 200-concurrency scores.

| Rank | Framework | Run s | req/s | P95 s | CPU s | RSS MB | Write MB | Threads | Score |
|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|
| 1 | vercel-ai-sdk | 3.901 | 256.3 | 3.846 | 4.266 | 390.9 | 0.129 | 16 | 81.7 |
| 2 | pygent-direct | 8.398 | 119.1 | 8.203 | 11.406 | 195.4 | 0.140 | 22 | 73.3 |
| 3 | pi-agent-core | 4.393 | 227.6 | 4.371 | 6.219 | 417.0 | 0.129 | 16 | 68.3 |
| 4 | openai-sdk | 28.171 | 35.5 | 27.318 | 24.625 | 249.6 | 0.147 | 41 | 50.0 |
| 5 | agentscope | 31.461 | 31.8 | 30.041 | 30.359 | 286.0 | 0.146 | 34 | 40.0 |
| 6 | langgraph | 33.067 | 30.2 | 32.858 | 32.750 | 292.1 | 0.147 | 45 | 20.0 |
| 7 | openai-agents | 37.503 | 26.7 | 37.316 | 36.828 | 279.7 | 0.147 | 48 | 16.7 |

All seven loop-lane entries completed 3,000/3,000 requests. Pygent direct ranks second and has the lowest RSS. Runtime costs are reported only in the durable lane above.

The older non-durable Mastra adapter does not qualify. Its three repetitions completed 574/1,000, 784/1,000 and 723/1,000 requests; all failures were `ECONNREFUSED` while opening connections to the local mock. Raising the mock listen backlog from 512 to 2,048 did not resolve the burst: a confirmation run completed 534/1,000. The separate native durable Mastra failure is reported in the runtime lane.

## Non-qualifying and retried runs

- LlamaIndex did not qualify: all three fresh high-load repetitions crossed the 4 GB RSS guard at 4,097-4,102 MB before completion. No older LlamaIndex high-load value is substituted.
- Strands completed 200/200 in its first two repetitions, then completed 195/200 because five requests reached its provider timeout. A clean replacement repetition completed 200/200 and is the third accepted sample. The failed repetition is retained in the raw result directory but excluded by the documented success rule.
- All ten refreshed high-load entries completed 200/200 in all ten fresh repetitions (100 accepted runs). Qualifying entries outside the refreshed cohort completed 200/200 in each of their three accepted baseline repetitions.

## Pygent 0.3.10 corrected A/B

The authorization fix was also run against the published Pygent 0.3.9 wheel, so the version comparison below uses identical successful-tool semantics. Every cell is a three-process median; negative change is better for all listed resource metrics.

| Load / mode | 0.3.9 run s | 0.3.10 run s | Run change | CPU change | RSS change | Write change |
|---|---:|---:|---:|---:|---:|---:|
| 200 direct | 1.309 | 1.234 | -5.7% | +7.1% | -4.3% | ~0% |
| 200 runtime disabled | 1.868 | 1.774 | -5.0% | -0.6% | -3.3% | ~0% |
| 200 runtime preferred | 3.433 | 3.862 | +12.5% | +17.2% | -3.8% | +0.1% |
| 1,000 direct | 8.072 | 8.398 | +4.0% | +16.2% | -1.7% | -0.1% |
| 1,000 runtime disabled | 11.076 | 10.409 | -6.0% | -4.8% | -3.4% | -0.5% |
| 1,000 runtime preferred | 20.402 | 19.133 | -6.2% | -10.1% | -1.7% | -0.6% |

Pygent 0.3.10 batches execution-claim lease renewals through the shared transaction queue. The corrected 1,000-concurrency result is consistent with that goal: preferred wall/P95 improve 6.2% and CPU improves 10.1%. The 200-concurrency preferred regression and 1,000-concurrency direct CPU increase mean the release is not an across-the-board speedup; three repetitions on a non-idle host are not enough to distinguish fixed overhead from host variance. The defensible conclusion is that 0.3.10 improves the high-load Runtime path while leaving SQLite write amplification essentially unchanged.

## Historical Pygent 0.3.9 durability profile

Pygent 0.3.9 removes the per-execution 50 ms SQLite cancellation polling seen in 0.3.8. A like-for-like 200-concurrency `runtime-preferred` Python `cProfile` comparison records:

| Version | Profile total s | Python calls | Heartbeat calls | SQLite cancellation queries |
|---|---:|---:|---:|---:|
| 0.3.8 | 37.723 | 10,614,532 | 95,965 | 94,844 |
| 0.3.9 | 29.363 | 8,963,550 | 1,200 | 0 |

In the ten-repetition resource benchmark, 0.3.9 reduces `runtime-preferred` wall time by 60.6%, P95 latency by 61.2% and CPU time by 59.2% relative to the same 0.3.8 wheel and environment. Durable event encoding and SQLite writes remain the main functional cost; this profile is diagnostic and its instrumented total is not interchangeable with the process-level benchmark table.

## Versions and limitations

Durable-lane rows use Pygent 0.3.10, LangGraph 1.2.10 with `langgraph-checkpoint-sqlite` 3.1.1, Mastra 1.64.0 with `@mastra/libsql` 1.22.3, OpenAI Agents 0.22.0 with DBOS 2.31.0 and `dbos-openai-agents` 0.3.0, and `@ai-sdk/workflow` 1.0.70 with Workflow SDK 4.8.5 Local World. Service-lane environments use AgentScope Runtime 1.1.6.post2 with AgentScope 2.0.7.post1, Agno 3.0.6 AgentOS, and Hayhooks 1.24.0 with Haystack 3.1.1. `@ai-sdk/workflow` 2.0.24 could not be installed because its published dependency graph requested the unavailable `@workflow/nest@5.0.0-beta.48`; 1.0.70 is the newest installable line in this environment. Loop baselines retain their recorded versions: OpenAI 2.53.0, Haystack 3.0.0, Agno 2.8.7, Microsoft Agent Framework core 1.13.0, Pi Agent Core 0.73.1, Mastra 1.57.0, AI SDK 7.0.58, OpenAI Agents 0.19.4, LangChain 1.3.14, LangGraph 1.2.10, Pydantic AI 2.27.0, AgentScope 2.0.6, Strands 1.51.0, smolagents 1.26.0, LlamaIndex 0.14.23, Qwen-Agent 0.0.34, Google ADK 2.6.3, OpenJiuwen 0.1.16 and OpenHands SDK 1.41.0.

This is a synthetic integration benchmark, not a model-quality test or production capacity guarantee. The local mock intentionally makes framework overhead visible by removing provider latency and variance. All runtime-enabled adapters independently validate the exact `[1, 2, 3]` tool sequence; older loop-only rows still require a full adapter audit before they can prove identical tool semantics. The 2026-09-08 refresh ran on a non-idle host, so cross-date regressions should be treated as inconclusive. Real provider rate limits, TLS distance, token generation latency and vendor retry behavior require a separate live benchmark.
