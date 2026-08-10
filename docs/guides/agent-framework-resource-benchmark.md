# Agent framework resource benchmark

Controlled synthetic run: local OpenAI-compatible mock, exactly three tool calls and one final answer per request; Windows, Python 3.12/3.13 or Node 24. Cold includes import and process startup. Serial and concurrent scenarios each execute eight requests in one process. Pygent 0.2.8 uses Python 3.13.11 with five fresh-process repetitions for cold/serial and twenty for all concurrent modes; other framework rows retain their original two repetitions.

| Framework / mode | Cold RSS MB | Cold CPU s | 8-conc RSS MB | 8-conc CPU s | 8-conc run s | req/s | speedup vs serial | write MB |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| pygent-direct | 58.4 | 1.17 | 60.5 | 1.23 | 0.572 | 13.99 | 0.59x | 0.001 |
| pygent-runtime-disabled | 63.0 | 1.27 | 65.0 | 1.30 | 0.585 | 13.68 | 0.60x | 0.001 |
| pygent-runtime-preferred | 64.3 | 1.23 | 65.7 | 1.62 | 0.468 | 17.10 | 1.79x | 3.968 |
| vercel-ai-sdk | 69.7 | 0.41 | 79.0 | 0.47 | 0.158 | 50.72 | 1.40x | 0.001 |
| pi-agent-core | 78.2 | 0.73 | 82.9 | 0.91 | 0.408 | 19.61 | 0.70x | 0.000 |
| openai-sdk | 103.3 | 2.60 | 105.6 | 2.43 | 0.925 | 8.64 | 0.94x | 0.005 |
| microsoft-agent-framework | 107.3 | 2.85 | 109.6 | 2.84 | 0.778 | 10.28 | 1.35x | 0.004 |
| smolagents | 118.6 | 2.77 | 120.6 | 6.06 | 1.965 | 4.07 | 1.11x | 0.018 |
| agno | 118.5 | 3.18 | 120.7 | 3.20 | 0.989 | 8.09 | 1.16x | 0.004 |
| agentscope | 134.0 | 3.04 | 136.5 | 3.55 | 0.973 | 8.23 | 0.92x | 0.004 |
| haystack | 135.2 | 2.84 | 137.3 | 4.16 | 1.154 | 6.93 | 0.95x | 0.005 |
| langgraph | 135.6 | 3.65 | 138.2 | 3.55 | 0.288 | 27.77 | 1.54x | 0.005 |
| langchain | 139.3 | 3.55 | 140.1 | 3.61 | 0.249 | 32.16 | 1.75x | 0.005 |
| openai-agents | 142.8 | 3.73 | 144.9 | 3.73 | 1.319 | 6.06 | 0.78x | 0.005 |
| pydantic-ai | 148.8 | 3.94 | 150.9 | 4.05 | 1.002 | 7.98 | 1.06x | 0.005 |
| strands | 127.2 | 3.13 | 172.0 | 4.91 | 2.603 | 3.07 | 0.92x | 0.002 |
| mastra | 182.8 | 1.39 | 186.4 | 1.47 | 0.276 | 28.98 | 1.49x | 0.001 |
| qwen-agent | 191.8 | 3.81 | 251.1 | 5.83 | 1.385 | 5.78 | 2.06x | 0.007 |
| openjiuwen | 254.2 | 4.98 | 256.8 | 6.26 | 1.224 | 6.54 | 1.03x | 0.335 |
| openhands | 271.1 | 6.27 | 272.1 | 7.68 | 1.904 | 4.20 | 1.07x | 0.017 |
| google-adk | 370.8 | 8.07 | 376.3 | 8.39 | 6.272 | 1.28 | 1.32x | 0.005 |
| llamaindex | 265.8 | 8.41 | 580.3 | 30.32 | 27.631 | 0.29 | 1.44x | 0.005 |

Notes: CPU is cumulative process-tree CPU time, not CPU percentage. Peak RSS is the sum over the process tree. The local mock server is excluded. Letta and Claude Agent SDK are excluded from this ranking because they could not run the same provider/workload contract.

## Main findings

- Lowest comparable 8-concurrency memory: Pygent direct 60.5 MB, Pygent Runtime disabled 65.0 MB, Pygent Runtime preferred 65.7 MB, Vercel AI SDK 79.0 MB, and Pi Agent Core 82.9 MB.
- Lowest comparable 8-concurrency CPU: Vercel AI SDK 0.47 CPU-s, Pi Agent Core 0.91 CPU-s, Pygent direct 1.23 CPU-s, Pygent Runtime disabled 1.30 CPU-s, and Mastra 1.47 CPU-s.
- LangChain and LangGraph have a larger Python baseline (about 138-140 MB), but good short-workload throughput in this test. Mastra is similarly fast but starts from about 186 MB.
- The heavy group is OpenJiuwen (257 MB), OpenHands (272 MB), Google ADK (376 MB), and LlamaIndex (580 MB at concurrency 8). These frameworks import or initialize much more than a minimal tool loop.
- LlamaIndex's outlier repeated in a third verification run: 534 MB peak RSS, 27.92 CPU-s, and 24.19 s framework run time. Google ADK also repeated as heavy: 376 MB peak RSS and 10.88 CPU-s in the third run.

## Pygent modes

Compared with Pygent direct execution for eight concurrent requests:

- Runtime with durability disabled adds about 4.5 MB RSS and 0.07 CPU-s; its 0.585 s run time is effectively level with direct execution's 0.572 s in this sample.
- Runtime preferred adds about 5.2 MB RSS and 0.39 CPU-s, while its batched journal path completes the concurrent run 0.104 s faster than direct execution.
- Preferred durability writes about 3.97 MB for the eight-request run, versus effectively zero for direct and disabled modes.

The memory result is strong: even preferred durability remains below every non-Pygent Python framework measured. Pygent 0.2.8 combines grouped SQLite journal commits with a lower-overhead provider stream path, removing one task allocation and wait/gather cycle per provider item. Execution-state bookkeeping remains visible in CPU. Because the mock has almost no network latency, relative framework overhead is intentionally a worst case; with a real multi-second model call, the same absolute bookkeeping cost becomes a much smaller percentage.

### Pygent 0.2.7 to 0.2.8

Same host, Python environment, dependencies, adapter, mock and sampler. Cold and serial values use five fresh-process repetitions per version; concurrent-8 uses twenty. Negative deltas are improvements.

| Mode | Scenario | Run 0.2.7 s | Run 0.2.8 s | Run delta | CPU delta | RSS delta | Write delta |
|---|---|---:|---:|---:|---:|---:|---:|
| direct | cold | 0.061 | 0.056 | -8.7% | -9.6% | -2.4% | 0.0% |
| direct | serial | 0.394 | 0.339 | -13.9% | -16.5% | -1.5% | +0.1% |
| direct | concurrent-8 | 0.589 | 0.572 | -2.9% | -19.1% | -0.3% | +0.1% |
| runtime-disabled | cold | 0.062 | 0.055 | -11.2% | -8.0% | -1.8% | +0.2% |
| runtime-disabled | serial | 0.454 | 0.352 | -22.5% | -24.3% | -1.9% | +0.1% |
| runtime-disabled | concurrent-8 | 0.590 | 0.585 | -0.9% | -20.2% | -0.3% | 0.0% |
| runtime-preferred | cold | 0.156 | 0.114 | -27.0% | -20.2% | -1.8% | 0.0% |
| runtime-preferred | serial | 1.172 | 0.839 | -28.4% | -25.8% | -2.0% | +0.4% |
| runtime-preferred | concurrent-8 | 0.601 | 0.468 | -22.1% | -17.2% | -0.3% | +0.4% |

## High load: 200 concurrent agents

Each run submits 200 agent executions at concurrency 200. Every execution performs three tool calls and one final answer, for 800 local model HTTP round trips and 600 tool executions per run. Pygent execution, model and tool capacity are explicitly raised to 200 so admission limits do not reject the workload. Results are medians from three fresh-process repetitions. The mock server is excluded from framework resource measurements.

Only frameworks completing 200/200 requests in all three repetitions qualify. The high-load score gives equal weight to peak RSS, cumulative CPU, total run time, P95 request latency and disk writes. Each metric is converted to a rank percentile; lower is better. Throughput is the inverse of total run time and is not counted twice. The function-neutral score excludes disk writes because durable journaling is not a comparable feature across frameworks.

| Rank | Framework / mode | Run s | req/s | P95 s | CPU s | RSS MB | Write MB | Score | Neutral |
|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|
| 1 | vercel-ai-sdk | 0.783 | 255.4 | 0.757 | 1.297 | 163.5 | 0.026 | 92.0 | 90.0 |
| 2 | pi-agent-core | 0.926 | 216.1 | 0.920 | 1.938 | 152.2 | 0.026 | 90.0 | 90.0 |
| 3 | mastra | 2.059 | 97.1 | 2.044 | 4.062 | 398.2 | 0.026 | 76.0 | 71.2 |
| 4 | microsoft-agent-framework | 3.199 | 62.5 | 2.558 | 5.172 | 147.1 | 0.035 | 73.0 | 80.0 |
| 5 | openai-sdk | 2.751 | 72.7 | 2.219 | 4.500 | 155.2 | 0.036 | 70.0 | 81.2 |
| 6 | agno | 3.877 | 51.6 | 3.029 | 5.859 | 158.7 | 0.033 | 68.0 | 68.8 |
| 7 | haystack | 3.493 | 57.3 | 2.752 | 5.484 | 170.6 | 0.034 | 67.0 | 70.0 |
| 8= | agentscope | 4.311 | 46.4 | 3.708 | 6.781 | 172.4 | 0.034 | 56.0 | 55.0 |
| 8= | pygent-direct | 8.045 | 24.9 | 7.983 | 8.875 | 120.6 | 0.028 | 56.0 | 50.0 |
| 10= | langgraph | 4.066 | 49.2 | 4.037 | 6.562 | 174.6 | 0.036 | 52.0 | 56.2 |
| 10= | pygent-runtime-disabled | 8.945 | 22.4 | 8.824 | 9.953 | 138.5 | 0.028 | 52.0 | 43.8 |
| 12= | langchain | 3.891 | 51.4 | 3.857 | 6.891 | 183.2 | 0.036 | 48.0 | 52.5 |
| 12= | openai-agents | 4.206 | 47.5 | 4.166 | 6.734 | 177.3 | 0.035 | 48.0 | 50.0 |
| 14 | smolagents | 5.707 | 35.0 | 4.146 | 8.641 | 129.0 | 0.377 | 47.0 | 55.0 |
| 15 | pydantic-ai | 5.163 | 38.7 | 5.058 | 7.828 | 191.6 | 0.035 | 42.0 | 40.0 |
| 16 | pygent-runtime-preferred | 9.576 | 20.9 | 9.466 | 11.016 | 87.6 | 89.952 | 35.0 | 43.8 |
| 17 | google-adk | 14.076 | 14.2 | 14.050 | 11.969 | 498.0 | 0.031 | 26.0 | 13.8 |
| 18 | openjiuwen | 11.769 | 17.0 | 10.631 | 15.859 | 296.7 | 8.222 | 17.0 | 20.0 |
| 19 | strands | 36.274 | 5.5 | 35.212 | 38.109 | 867.4 | 0.032 | 16.0 | 2.5 |
| 20 | qwen-agent | 14.668 | 13.6 | 12.035 | 80.531 | 774.2 | 0.104 | 10.0 | 7.5 |
| 21 | openhands | 34.561 | 5.8 | 30.615 | 39.891 | 338.5 | 0.409 | 9.0 | 8.8 |

LlamaIndex did not qualify: all three runs crossed the 4 GB RSS safety limit before completing, at 4,096-4,110 MB after 74-88 seconds. Its subprocess tree was terminated and joined by the benchmark resource guard.

Pygent remains memory-efficient under load: direct is second-lowest at 120.6 MB, and preferred is lowest at 87.6 MB. A likely explanation is that journal serialization paces the active stream workload, but this run did not instrument simultaneous live stream count. Its throughput scales poorly at concurrency 200, however. Direct reaches 24.9 requests/s, versus 255.4 for Vercel AI SDK and 72.7 for the OpenAI SDK. Preferred writes about 90 MB of execution journal for the 200 executions. The high-load result therefore exposes model-stream/event-loop CPU and scheduling as Pygent's next optimization target rather than memory retention.

## Practical selection

- Choose Pygent Runtime when durable history, admission control, cancellation, capacity, and tool governance replace application code you would otherwise maintain. Use direct execution for short-lived or stateless paths that do not need those guarantees.
- Choose Pi Agent Core or Vercel AI SDK when the priority is a small Node runtime and a simple in-process agent loop. The installed `@mariozechner/*` Pi packages emitted deprecation warnings and now point to `@earendil-works/*`, which is a migration risk to account for.
- LangGraph is a good middle ground when graph orchestration is the main requirement. LangChain is slightly faster here but has a similar memory/import footprint.
- Mastra gives strong throughput but not a small memory baseline. Microsoft Agent Framework is the most resource-efficient of the larger Python agent abstractions in this run.
- OpenHands, OpenJiuwen, Google ADK, and LlamaIndex should be selected for their broader platform features, not to minimize a small API service's agent-loop footprint.

## Incomparable or failed-to-start entries

- Letta 0.16.8 is service-shaped rather than an in-process loop. Import alone used 186.4 MB RSS, 6.81 CPU-s, and 8.62 s wall time. A clean install first resolved MCP 2.0, which failed at startup because FastMCP expected the MCP 1.x exception API. Pinning MCP below 2 fixed that conflict, but the server then required a reachable PostgreSQL service on this code path, so it was not placed in the execution ranking. Its isolated environment occupied 653.5 MB.
- Claude Agent SDK 0.2.134 is tied to the Claude CLI/provider protocol and could not use the OpenAI-compatible mock. Import alone used 58.6 MB RSS, 1.31 CPU-s, and 1.62 s wall time. The distribution itself occupied 274.2 MB, mostly from a bundled 287 MB `claude.exe`; no provider call was made.

## Versions and test caveats

Python: OpenAI 2.53.0, OpenAI Agents 0.19.4, LangChain 1.3.14, LangGraph 1.2.10, Pydantic AI 2.27.0, AgentScope 2.0.6, Strands 1.51.0, Microsoft Agent Framework 1.13.0, Agno 2.8.7, smolagents 1.26.0, LlamaIndex 0.14.23, Haystack 3.0.0, Qwen-Agent 0.0.34, Google ADK 2.6.3, Pygent 0.2.8, OpenJiuwen 0.1.16, and OpenHands 1.41.0. Node: Pi Agent Core 0.73.1, Mastra 1.57.0, and AI SDK 7.0.58.

The primary ranking is a synthetic integration benchmark. Pygent 0.2.8 results are medians from the newer repeated run; other framework results retain their original two fresh-process repetitions. It measures framework and local HTTP/tool-loop costs without provider variance; it is not a model-quality test or a production capacity claim. Very short mock calls make scheduling noise visible, so small cross-framework timing gaps should be treated as directional. CPU totals include import/initialization. Shared benchmark environments make per-framework installation-size attribution invalid, so only isolated environment sizes are reported.
