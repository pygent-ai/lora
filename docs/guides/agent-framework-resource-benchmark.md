# Agent framework resource benchmark

Controlled synthetic run: local OpenAI-compatible mock, exactly three tool calls and one final answer per request; Windows, Python 3.12/3.13 or Node 24. Cold includes import and process startup. Serial and concurrent scenarios each execute eight requests in one process. Pygent 0.2.10 uses Python 3.13.11 with five fresh-process repetitions per short scenario; other framework rows retain their original two repetitions. The 0.2.9-to-0.2.10 comparison uses cloned environments with identical non-Pygent dependencies and differs only in the installed Pygent wheel.

| Framework / mode | Cold RSS MB | Cold CPU s | 8-conc RSS MB | 8-conc CPU s | 8-conc run s | req/s | speedup vs serial | write MB |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| pygent-direct | 61.1 | 0.81 | 62.1 | 0.89 | 0.124 | 64.70 | 2.24x | 0.001 |
| pygent-runtime-disabled | 65.7 | 0.92 | 66.9 | 0.97 | 0.134 | 59.61 | 2.15x | 0.001 |
| pygent-runtime-preferred | 66.6 | 0.91 | 68.5 | 1.16 | 0.206 | 38.75 | 2.48x | 1.862 |
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

- Lowest comparable 8-concurrency memory is now Pygent direct at 62.1 MB, Runtime disabled at 66.9 MB, Runtime preferred at 68.5 MB, Vercel AI SDK at 79.0 MB, and Pi Agent Core at 82.9 MB.
- Lowest comparable 8-concurrency CPU remains Vercel AI SDK at 0.47 CPU-s, followed by Pygent direct at 0.89 CPU-s, Pi Agent Core at 0.91 CPU-s, Pygent Runtime disabled at 0.97 CPU-s, and Runtime preferred at 1.16 CPU-s.
- LangChain and LangGraph have a larger Python baseline (about 138-140 MB), but good short-workload throughput in this test. Mastra is similarly fast but starts from about 186 MB.
- The heavy group is OpenJiuwen (257 MB), OpenHands (272 MB), Google ADK (376 MB), and LlamaIndex (580 MB at concurrency 8). These frameworks import or initialize much more than a minimal tool loop.
- LlamaIndex's outlier repeated in a third verification run: 534 MB peak RSS, 27.92 CPU-s, and 24.19 s framework run time. Google ADK also repeated as heavy: 376 MB peak RSS and 10.88 CPU-s in the third run.

## Pygent modes

Compared with Pygent direct execution for eight concurrent requests:

- Runtime with durability disabled adds about 4.9 MB RSS and 0.08 CPU-s; its 0.134 s run time is 0.011 s slower than direct execution.
- Runtime preferred adds about 6.4 MB RSS and 0.27 CPU-s over direct; its 0.206 s run time is 0.083 s slower than direct execution.
- Preferred durability writes about 1.86 MB for the eight-request run, versus effectively zero for direct and disabled modes.

Pygent 0.2.10 retains the eight HTTP pool shards introduced in 0.2.9 but shares one TLS context across them. In the strict A/B, direct client/framework construction falls from a 4.79 s median to 0.71 s, CPU falls 76-84% across short scenarios, and RSS falls 22-24%. Lazy shard construction or a public shard-count setting could still reduce the remaining short-lived and low-concurrency baseline. Because the mock has almost no network latency, relative framework overhead is intentionally a worst case; with a real multi-second model call, the same absolute bookkeeping cost becomes a smaller percentage.

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

### Pygent 0.2.8 to 0.2.9

Same adapter, mock, sampler and Python dependency set; only the Pygent wheel changes. Cold and serial values use five fresh-process repetitions per version; concurrent-8 uses twenty. Negative deltas are improvements. CPU includes import and initialization, so the eager eight-shard HTTP client is intentionally visible.

| Mode | Scenario | Run 0.2.8 s | Run 0.2.9 s | Run delta | CPU delta | RSS delta | Write delta |
|---|---|---:|---:|---:|---:|---:|---:|
| direct | cold | 0.065 | 0.045 | -30.4% | +176.3% | +28.0% | 0.0% |
| direct | serial | 0.444 | 0.294 | -33.8% | +155.6% | +27.5% | -0.3% |
| direct | concurrent-8 | 0.160 | 0.186 | +16.3% | +347.7% | +25.7% | -0.1% |
| runtime-disabled | cold | 0.057 | 0.046 | -19.8% | +235.6% | +27.1% | -0.4% |
| runtime-disabled | serial | 0.408 | 0.471 | +15.4% | +240.2% | +26.0% | -0.1% |
| runtime-disabled | concurrent-8 | 0.174 | 0.221 | +26.9% | +353.3% | +24.3% | -0.1% |
| runtime-preferred | cold | 0.179 | 0.101 | -43.8% | +179.5% | +26.0% | -31.1% |
| runtime-preferred | serial | 1.076 | 0.692 | -35.6% | +151.6% | +25.4% | -39.8% |
| runtime-preferred | concurrent-8 | 0.503 | 0.290 | -42.4% | +275.7% | +25.6% | -52.8% |

### Pygent 0.2.9 to 0.2.10

Both environments use the same adapter, mock, sampler, Python and dependency set; only the Pygent wheel changes. Each cell is the median of five fresh-process repetitions. Negative deltas are improvements.

| Mode | Scenario | Run 0.2.9 s | Run 0.2.10 s | Run delta | CPU delta | RSS delta | Write delta |
|---|---|---:|---:|---:|---:|---:|---:|
| direct | cold | 0.052 | 0.043 | -18.9% | -82.8% | -23.5% | 0.0% |
| direct | serial | 0.338 | 0.277 | -17.8% | -83.9% | -23.6% | +0.1% |
| direct | concurrent-8 | 0.127 | 0.124 | -2.8% | -81.4% | -23.7% | +0.2% |
| runtime-disabled | cold | 0.045 | 0.045 | -0.1% | -80.6% | -22.3% | 0.0% |
| runtime-disabled | serial | 0.303 | 0.289 | -4.7% | -80.7% | -22.4% | -0.1% |
| runtime-disabled | concurrent-8 | 0.140 | 0.134 | -3.8% | -79.3% | -22.2% | +0.1% |
| runtime-preferred | cold | 0.068 | 0.074 | +8.8% | -78.7% | -22.3% | 0.0% |
| runtime-preferred | serial | 0.503 | 0.512 | +1.9% | -75.6% | -21.9% | -1.2% |
| runtime-preferred | concurrent-8 | 0.227 | 0.206 | -9.1% | -76.8% | -22.1% | +0.2% |

## High load: 200 concurrent agents

Each run submits 200 agent executions at concurrency 200. Every execution performs three tool calls and one final answer, for 800 local model HTTP round trips and 600 tool executions per run. Pygent execution, model and tool capacity are explicitly raised to 200 so admission limits do not reject the workload. Results are medians from three fresh-process repetitions. The mock server is excluded from framework resource measurements.

Only frameworks completing 200/200 requests in all three repetitions qualify. The high-load score gives equal weight to peak RSS, cumulative CPU, total run time, P95 request latency and disk writes. Each metric is converted to a rank percentile; lower is better. Throughput is the inverse of total run time and is not counted twice. The function-neutral score excludes disk writes because durable journaling is not a comparable feature across frameworks.

| Rank | Framework / mode | Run s | req/s | P95 s | CPU s | RSS MB | Write MB | Score | Neutral |
|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|
| 1 | vercel-ai-sdk | 0.783 | 255.4 | 0.757 | 1.297 | 163.5 | 0.026 | 92.0 | 90.0 |
| 2 | pi-agent-core | 0.926 | 216.1 | 0.920 | 1.938 | 152.2 | 0.026 | 90.0 | 90.0 |
| 3 | pygent-direct | 2.743 | 72.9 | 2.682 | 3.469 | 115.0 | 0.028 | 85.0 | 86.2 |
| 4 | pygent-runtime-disabled | 3.301 | 60.6 | 3.209 | 4.094 | 122.8 | 0.028 | 77.0 | 75.0 |
| 5 | mastra | 2.059 | 97.1 | 2.044 | 4.062 | 398.2 | 0.026 | 75.0 | 70.0 |
| 6 | microsoft-agent-framework | 3.199 | 62.5 | 2.558 | 5.172 | 147.1 | 0.035 | 70.0 | 76.2 |
| 7 | openai-sdk | 2.751 | 72.7 | 2.219 | 4.500 | 155.2 | 0.036 | 67.0 | 77.5 |
| 8 | agno | 3.877 | 51.6 | 3.029 | 5.859 | 158.7 | 0.033 | 62.0 | 61.2 |
| 9 | haystack | 3.493 | 57.3 | 2.752 | 5.484 | 170.6 | 0.034 | 61.0 | 62.5 |
| 10 | agentscope | 4.311 | 46.4 | 3.708 | 6.781 | 172.4 | 0.034 | 49.0 | 46.2 |
| 11 | pygent-runtime-preferred | 4.514 | 44.3 | 4.413 | 5.297 | 90.7 | 66.064 | 46.0 | 57.5 |
| 12 | langgraph | 4.066 | 49.2 | 4.037 | 6.562 | 174.6 | 0.036 | 45.0 | 47.5 |
| 13 | langchain | 3.891 | 51.4 | 3.857 | 6.891 | 183.2 | 0.036 | 41.0 | 43.8 |
| 14 | openai-agents | 4.206 | 47.5 | 4.166 | 6.734 | 177.3 | 0.035 | 41.0 | 41.2 |
| 15 | smolagents | 5.707 | 35.0 | 4.146 | 8.641 | 129.0 | 0.377 | 38.0 | 43.8 |
| 16 | pydantic-ai | 5.163 | 38.7 | 5.058 | 7.828 | 191.6 | 0.035 | 33.0 | 28.8 |
| 17 | google-adk | 14.076 | 14.2 | 14.050 | 11.969 | 498.0 | 0.031 | 26.0 | 13.8 |
| 18 | openjiuwen | 11.769 | 17.0 | 10.631 | 15.859 | 296.7 | 8.222 | 17.0 | 20.0 |
| 19 | strands | 36.274 | 5.5 | 35.212 | 38.109 | 867.4 | 0.032 | 16.0 | 2.5 |
| 20 | qwen-agent | 14.668 | 13.6 | 12.035 | 80.531 | 774.2 | 0.104 | 10.0 | 7.5 |
| 21 | openhands | 34.561 | 5.8 | 30.615 | 39.891 | 338.5 | 0.409 | 9.0 | 8.8 |

LlamaIndex did not qualify: all three runs crossed the 4 GB RSS safety limit before completing, at 4,096-4,110 MB after 74-88 seconds. Its subprocess tree was terminated and joined by the benchmark resource guard.

Pygent 0.2.10 moves direct execution to third overall and Runtime disabled to fourth. Direct reaches 72.9 requests/s with 115.0 MB RSS, while Runtime disabled reaches 60.6 requests/s with 122.8 MB RSS. Preferred reaches 44.3 requests/s while retaining durable history and remains the lowest-memory qualifying mode at 90.7 MB. The strict 0.2.9 A/B shows that throughput is essentially unchanged while process CPU and memory are materially lower.

### Pygent 0.2.8 to 0.2.9 at 200 concurrency

Both versions complete 200/200 executions in all three fresh-process repetitions. Values are medians; P95 is calculated per run and then medianed.

| Mode | Run 0.2.8 s | Run 0.2.9 s | Run delta | Throughput delta | P95 delta | CPU delta | RSS delta | Write delta |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| direct | 8.350 | 4.080 | -51.1% | +104.7% | -51.9% | -11.2% | +1.2% | +0.4% |
| runtime-disabled | 9.492 | 3.607 | -62.0% | +163.1% | -62.6% | -21.3% | -2.7% | +0.5% |
| runtime-preferred | 9.741 | 5.023 | -48.4% | +93.9% | -48.8% | -12.7% | +22.0% | -26.5% |

The CPU profiles explain the change. Direct `httpcore` pool-assignment self-time falls from 3.491 s to 0.027 s, and total profiled function calls fall from 58.8 million to 10.1 million. Preferred JSON freeze/thaw calls fall from 4.43 million to 1.91 million, event-loop callbacks from 170,376 to 61,917, and `_run_once` iterations from 32,113 to 5,360. The remaining issue is configuration shape: fixed eager eight-way sharding is excellent for 200 active streams but unnecessarily expensive for command-line, serverless and low-concurrency processes.

### Pygent 0.2.9 to 0.2.10 at 200 concurrency

Both versions complete 200/200 executions in all three fresh-process repetitions. Values are medians; P95 is calculated per run and then medianed.

| Mode | Run 0.2.9 s | Run 0.2.10 s | Run delta | Throughput delta | P95 delta | CPU delta | RSS delta | Write delta |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| direct | 2.672 | 2.743 | +2.7% | -2.6% | +2.4% | -52.6% | -13.2% | 0.0% |
| runtime-disabled | 3.399 | 3.301 | -2.9% | +3.0% | -2.8% | -48.3% | -13.6% | 0.0% |
| runtime-preferred | 4.466 | 4.514 | +1.1% | -1.0% | +0.9% | -41.8% | -18.0% | +0.2% |

CPU profiles attribute most of the improvement to TLS and event projection work. SSL trust loading falls from 24 calls per process to one, reducing profiled self-time from 3.27-4.02 s to 0.14-0.15 s. Direct execution condition notifications fall from 18,601 to 6,801 when no journal subscriber needs a wakeup. Preferred top-level JSON freezes fall from 50,606 to 43,406 by reusing prepared immutable payloads. Total profiled CPU time falls 29.6% for direct and 31.1% for preferred; process-tree sampling shows the larger 41.8-52.6% CPU reduction without profiler overhead.

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

Python: OpenAI 2.53.0, OpenAI Agents 0.19.4, LangChain 1.3.14, LangGraph 1.2.10, Pydantic AI 2.27.0, AgentScope 2.0.6, Strands 1.51.0, Microsoft Agent Framework 1.13.0, Agno 2.8.7, smolagents 1.26.0, LlamaIndex 0.14.23, Haystack 3.0.0, Qwen-Agent 0.0.34, Google ADK 2.6.3, Pygent 0.2.10, OpenJiuwen 0.1.16, and OpenHands 1.41.0. Node: Pi Agent Core 0.73.1, Mastra 1.57.0, and AI SDK 7.0.58.

The primary ranking is a synthetic integration benchmark. Pygent 0.2.10 results are medians from the newer repeated run; other framework results retain their original two fresh-process repetitions. It measures framework and local HTTP/tool-loop costs without provider variance; it is not a model-quality test or a production capacity claim. Very short mock calls make scheduling noise visible, so small cross-framework timing gaps should be treated as directional. CPU totals include import/initialization. Shared benchmark environments make per-framework installation-size attribution invalid, so only isolated environment sizes are reported.
