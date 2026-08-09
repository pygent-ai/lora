# Agent framework resource benchmark

Controlled synthetic run: local OpenAI-compatible mock, exactly three tool calls and one final answer per request; Windows, Python 3.12 or Node 24. Cold includes import and process startup. Serial and concurrent scenarios each execute eight requests in one process. Pygent 0.2.7 uses five fresh-process repetitions for cold/serial and twenty for the two Runtime concurrent modes; other framework rows use two repetitions.

| Framework / mode | Cold RSS MB | Cold CPU s | 8-conc RSS MB | 8-conc CPU s | 8-conc run s | req/s | speedup vs serial | write MB |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| pygent-direct | 58.4 | 0.92 | 60.6 | 1.20 | 0.582 | 13.75 | 0.63x | 0.001 |
| pygent-runtime-disabled | 62.8 | 1.11 | 64.9 | 1.34 | 0.576 | 13.88 | 0.65x | 0.001 |
| pygent-runtime-preferred | 64.0 | 1.19 | 66.0 | 1.56 | 0.485 | 16.50 | 1.82x | 3.958 |
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

- Lowest comparable 8-concurrency memory: Pygent direct 60.6 MB, Pygent Runtime disabled 64.9 MB, Pygent Runtime preferred 66.0 MB, Vercel AI SDK 79.0 MB, and Pi Agent Core 82.9 MB.
- Lowest comparable 8-concurrency CPU: Vercel AI SDK 0.47 CPU-s, Pi Agent Core 0.91 CPU-s, Pygent direct 1.20 CPU-s, Pygent Runtime disabled 1.34 CPU-s, and Mastra 1.47 CPU-s.
- LangChain and LangGraph have a larger Python baseline (about 138-140 MB), but good short-workload throughput in this test. Mastra is similarly fast but starts from about 186 MB.
- The heavy group is OpenJiuwen (257 MB), OpenHands (272 MB), Google ADK (376 MB), and LlamaIndex (580 MB at concurrency 8). These frameworks import or initialize much more than a minimal tool loop.
- LlamaIndex's outlier repeated in a third verification run: 534 MB peak RSS, 27.92 CPU-s, and 24.19 s framework run time. Google ADK also repeated as heavy: 376 MB peak RSS and 10.88 CPU-s in the third run.

## Pygent modes

Compared with Pygent direct execution for eight concurrent requests:

- Runtime with durability disabled adds about 4.3 MB RSS and 0.13 CPU-s; its 0.576 s run time is effectively level with direct execution's 0.582 s in this sample.
- Runtime preferred adds about 5.4 MB RSS and 0.36 CPU-s, while its batched journal path completes the concurrent run 0.097 s faster than direct execution.
- Preferred durability writes about 3.96 MB for the eight-request run, versus effectively zero for direct and disabled modes.

The memory result is strong: even preferred durability remains below every non-Pygent Python framework measured. Pygent 0.2.7's grouped SQLite journal commits materially reduce concurrent durability writes and remove the previous concurrent latency penalty in this run, while execution-state bookkeeping remains visible in CPU. Because the mock has almost no network latency, relative framework overhead is intentionally a worst case; with a real multi-second model call, the same absolute bookkeeping cost becomes a much smaller percentage.

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

Python: OpenAI 2.53.0, OpenAI Agents 0.19.4, LangChain 1.3.14, LangGraph 1.2.10, Pydantic AI 2.27.0, AgentScope 2.0.6, Strands 1.51.0, Microsoft Agent Framework 1.13.0, Agno 2.8.7, smolagents 1.26.0, LlamaIndex 0.14.23, Haystack 3.0.0, Qwen-Agent 0.0.34, Google ADK 2.6.3, Pygent 0.2.7, OpenJiuwen 0.1.16, and OpenHands 1.41.0. Node: Pi Agent Core 0.73.1, Mastra 1.57.0, and AI SDK 7.0.58.

The primary ranking is a synthetic integration benchmark. Pygent 0.2.7 results are medians from the newer repeated run; other framework results retain their original two fresh-process repetitions. It measures framework and local HTTP/tool-loop costs without provider variance; it is not a model-quality test or a production capacity claim. Very short mock calls make scheduling noise visible, so small cross-framework timing gaps should be treated as directional. CPU totals include import/initialization. Shared benchmark environments make per-framework installation-size attribution invalid, so only isolated environment sizes are reported.
