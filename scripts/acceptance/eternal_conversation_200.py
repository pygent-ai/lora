from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import time
import traceback
import zipfile
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pygent import thaw_json

from lora.config import load_run_config
from lora.core.io import append_jsonl, read_json, read_jsonl_snapshot, write_json, write_json_atomic
from lora.runtime import LoraRuntimeService
from lora.runtime.agent.common import DEFAULT_REACT_MAX_STEPS
from lora.runtime.eternal_conversation import load_projection
from lora.schema import SessionRef
from lora.sessions import SessionManager


@dataclass(frozen=True)
class Component:
    name: str
    file: str
    contract: str


COMPONENTS = (
    Component("canonical envelope", "canonical_envelope.py", "typed event envelopes with deterministic field order"),
    Component("append-only journal", "journal.py", "durable append-before-ack storage; never process-local-only"),
    Component("cursor checkpoint", "checkpoint.py", "monotonic durable cursors with compare-and-swap"),
    Component("idempotency registry", "idempotency.py", "stable request keys and replay-safe outcomes"),
    Component("retry budget", "retry_budget.py", "bounded attempts with explicit terminal errors"),
    Component("conflict detector", "conflicts.py", "surface incompatible requirements before mutation"),
    Component("schema migrator", "migrations.py", "forward migrations that retain readable old data"),
    Component("lease manager", "leases.py", "UTC expiries and fencing tokens"),
    Component("snapshot projector", "snapshots.py", "finite projections without deleting source events"),
    Component("query index", "query_index.py", "deterministic indexed lookup plus verifiable fallback"),
    Component("audit exporter", "audit.py", "complete observable evidence with stable ordering"),
    Component("policy evaluator", "policy.py", "fail-closed decisions with actionable reasons"),
    Component("command router", "commands.py", "strict commands and backwards-compatible aliases"),
    Component("config loader", "config.py", "validated configuration with no silent fallback"),
    Component("checksum manifest", "manifest.py", "canonical UTF-8 JSON, sorted keys, and one LF"),
    Component("offline packager", "offline.py", "release inputs resolve without network access"),
    Component("UTC clock", "clock.py", "all persisted timestamps are timezone-aware UTC"),
    Component("stable serializer", "serializer.py", "byte-stable output across machines and timezones"),
    Component("error taxonomy", "errors.py", "typed errors; never silently swallow invalid state"),
    Component("metrics reducer", "metrics.py", "replayable counters derived from durable events"),
    Component("backup verifier", "backup.py", "content-hash verification before declaring success"),
    Component("restore planner", "restore.py", "dry-run plans and atomic activation"),
    Component("capability registry", "capabilities.py", "explicit versioned capabilities"),
    Component("compatibility facade", "compat.py", "preserve public v1 callers while adding v2"),
    Component("release gate", "release_gate.py", "offline reproducibility and complete targeted tests"),
)

PHASES = (
    "contract",
    "edge cases",
    "integration",
    "observability",
    "performance",
    "failure recovery",
    "change request",
    "release proof",
)

BLIND_CONFLICT_REQUESTS = {
    "append-only journal": (
        "日志这块磁盘写有点慢。先改成内存里记下就返回，磁盘放后台慢慢写，原来那条同步落盘路径也一起删了吧。"
    ),
    "offline packager": (
        "打包这块维护本地缓存太麻烦了，改成发布时直接从网上拉最新依赖吧，离线那套门禁也可以拿掉。"
    ),
    "UTC clock": (
        "开发机上看时间不太直观，持久化时间直接存机器本地时间吧，offset 也别带了，磁盘格式先保持现在这样。"
    ),
    "error taxonomy": (
        "校验失败老是打断调用链。这里干脆都吞掉并返回成功吧，调用方也不用再看到那些具体异常类型了。"
    ),
    "compatibility facade": (
        "v2 现在看着挺稳定的，把 v1 的入口和兼容别名直接删掉吧，免得后面还要维护两套。"
    ),
}
MEMORY_SEEDS = {
    "append-only journal": (
        "还有个协作约定：我们把“返回成功前必须真正落盘”叫 Atlas。以后除非我明确说覆盖 Atlas，"
        "否则都按它走；这个内部代号不用写进代码或文档。"
    ),
    "offline packager": (
        "目标部署环境内部叫 Northstar，它在运行和发布阶段都完全断网。这个环境名只用于我们沟通，"
        "不用写进仓库。"
    ),
    "UTC clock": (
        "跨系统对账约定内部叫 Meridian：所有持久化时间都用带时区的 UTC。代号不用落到代码里。"
    ),
    "error taxonomy": (
        "调用方会按具体异常类型做恢复，这个兼容约定内部叫 Beacon。保留类型信息，代号不用写进源码。"
    ),
    "compatibility facade": (
        "v1 的口头支持窗口至少到 0.4 正式发布之后；现在先保留，暂时不用把这条写进迁移文档。"
    ),
}
PROBE_MARKERS = {
    "append-only journal": "Atlas",
    "offline packager": "Northstar",
    "UTC clock": "Meridian",
    "error taxonomy": "Beacon",
    "compatibility facade": "0.4",
}

EDGE_CASE_REPORTS = {
    "canonical envelope": "有人把 NaN 塞进 payload 后仍然序列化成功了；这里应该明确拒绝非有限浮点数",
    "append-only journal": "进程中断会留下半行记录；重新打开时要忽略未完成尾行，但不能吞掉中间损坏",
    "cursor checkpoint": "bool 现在会被当成 0/1 游标收进去；这种值应该直接拒绝",
    "idempotency registry": "只含空白的 request key 还能注册；请把它当成无效输入",
    "retry budget": "attempts 传 bool 会混进整数分支；这里需要明确挡住",
    "conflict detector": "同一条要求重复出现时会被自己判成冲突；重复项应该合并处理",
    "schema migrator": "读到比当前版本更新的数据时还会继续跑；这种未来版本要明确失败",
    "lease manager": "naive datetime 能混进过期时间；租约边界只接受带时区时间",
    "snapshot projector": "空事件流现在拿不到一个可用的初始快照；补上这个基础行为",
    "query index": "同一个键重复写入相同位置会产生重复命中；结果里应该去重且顺序稳定",
    "audit exporter": "记录里出现集合时导出顺序不稳定；请把输出固定下来",
    "policy evaluator": "未知 policy 名称现在走了默认放行；这里应该 fail closed",
    "command router": "命令前后多一个空格会绕过严格校验；空白处理要一致",
    "config loader": "配置里的 bool 会被整数选项接受；把这类类型混淆挡住",
    "checksum manifest": "Windows 换行文件算出来的 manifest 在不同机器上不一致；统一输入规范",
    "offline packager": "依赖清单里重复条目会被打包两次；去重后还要保持确定顺序",
    "UTC clock": "有人传了 bool 作为时间戳，结果被当成 0/1 秒接受了；这种输入要明确报错",
    "stable serializer": "payload 里有 NaN 时仍能产出看似合法的字节；非有限数字必须拒绝",
    "error taxonomy": "第三方异常把 terminal 写成字符串 'false' 时也会被判成终止错误；标志位只认真正的 bool",
    "metrics reducer": "重复 replay 同一个事件会把计数累加两遍；按事件标识保证幂等",
    "backup verifier": "空备份目录现在也会报告验证成功；没有可验证内容时应当失败",
    "restore planner": "源路径和目标路径相同时仍生成覆盖步骤；这种计划要拒绝",
    "capability registry": "查询最低版本时传 True 会被当成版本 1；版本参数要和注册时一样明确拒绝 bool",
    "compatibility facade": "supports(True) 现在会回答支持 v1；版本探测不能把 bool 当成整数版本",
    "release gate": "reproducible_check 返回字符串 'false' 时 gate 仍会当成通过；检查结果必须是真正的 bool",
}

INTEGRATION_REPORTS = {
    "checksum manifest": (
        "manifest 配置这边需要一个很薄的入口。请在 quarry/config_manifest.py 里公开 "
        "build_configured_manifest(data)：用 ConfigSchema 校验 data，只接受必填的 paths（list 或 dict）"
        "和默认 sha256 的 algorithm，然后直接交给 build_manifest。未知字段、缺字段继续报 ConfigError，"
        "未知算法和文件问题继续报 ManifestError；补上成功和错误传播的集成测试。"
    ),
}

PERFORMANCE_REPORTS = {
    "append-only journal": (
        "状态页会连续调用 len(journal)，现在每次都会重新打开并解析整份日志，一万条以后很明显。"
        "把完整记录数在打开时算一次，成功 append 后递增，让后续 len() 不再重扫文件；"
        "保留尾部半行不计数的现有语义，并用回归测试证明重复 len 不会再次走 read_all。"
    ),
    "lease manager": (
        "lease 的 acquire/release 每次持久化时已经有打开的句柄，却又让 dump_envelope 重新打开同一路径，"
        "随后还在旧句柄上 flush/fsync。请把 canonical 内容一次写进现有句柄并在同一句柄上落盘，"
        "保持磁盘格式和 fencing token 语义不变，再补一个关闭重开后仍能读回的回归测试。"
    ),
    "offline packager": (
        "一个 bundle 里很多文件共用同一层目录，现在 package_release 会对每个文件都重复调用 "
        "dest.parent.mkdir。请在单次打包里记住已经准备好的父目录，同一目录只创建一次；"
        "复制顺序、离线约束和 manifest 内容都保持不变，并补个能钉住重复 mkdir 次数的测试。"
    ),
    "UTC clock": (
        "批量导入里经常重复出现同一个 ISO 时间字符串，parse_utc 每次都会重新走 fromisoformat。"
        "给纯字符串解析加一个有上限的缓存，重复值复用规范化后的 UTC datetime；"
        "无效输入、naive 时间和 coerce_utc 的现有行为都不能变，并补个能看到 cache hit 的测试。"
    ),
    "backup verifier": (
        "状态轮询会反复调用同一个 BackupVerifier.verify，大 manifest 每次都重新读盘和解析，"
        "但文件内容校验本身仍然必须每次执行。请按 manifest 的 mtime_ns 和 size 缓存已解析内容，"
        "签名变化就重新加载；校验结果和异常语义保持不变，并测试未变化只加载一次、变化后会失效。"
    ),
    "compatibility facade": (
        "迁移脚本会对同一个 facade 方法连续调用上万次，现在每次都重新解析 surface 和 callable。"
        "加一个小的 call_many(name, calls, version=None) 批量入口，每项是 args/kwargs，单批只解析一次目标方法，"
        "结果顺序和逐次 call 一致，遇到无效方法或某项异常仍原样停止；补上等价性和只解析一次的测试。"
    ),
    "release gate": (
        "发布面板会用多组 tests_run 预览同一个 release gate，现在每组都会重复执行昂贵的 "
        "reproducible_check。加一个 evaluate_many(test_runs) 批量入口，一批里只做一次 reproducibility 检查，"
        "每组仍独立计算完整性并按输入顺序返回 GateResult；异常和单次 evaluate 的语义保持一致，补测试。"
    ),
}

FAILURE_RECOVERY_REPORTS = {
    "schema migrator": (
        "SchemaMigrator 的临时文件已经会 fsync 再 os.replace，但 rename 后没有同步父目录，断电时目录项仍可能丢。"
        "在支持目录 fsync 的平台上按“临时文件落盘 → replace → 父目录 fsync”的顺序完成提交；"
        "不支持时保持可用，现有原子替换和重跑幂等语义不变。用 monkeypatch 测试提交顺序和失败清理。"
    ),
    "command router": (
        "这个 router 明确是纯内存，不需要硬加文件恢复；实际问题在启动时的批量注册。"
        "如果后面的 alias 无效，前面已经注册的 command 会留下一半状态。加一个 configure_batch(commands, aliases) "
        "入口，在临时副本上完整校验后一次提交；失败时原 router 不变，成功批次原样重放应是 no-op。"
        "保留现有单项 API，并测试中途失败、成功提交和重复恢复。"
    ),
}

CHANGE_REQUEST_REPORTS = {
    "canonical envelope": (
        "事件导入要加一个明确的 v2 批量入口 build_envelopes_v2(events)。每项包含 payload、event_type，"
        "并可带 event_id/time；按输入顺序逐项复用 build_envelope，第一项无效数据就原样报错，不返回半批结果。"
        "现有 build_envelope 和全部旧导出保持不变，再提供 build_envelope_v1 兼容别名；补批量、失败和旧入口测试，"
        "README 里留一段简短迁移示例。"
    ),
}

IGNORED_PROJECT_PARTS = {".git", ".lora", ".pytest_cache", ".venv", "__pycache__"}
TRACKED_PROJECT_SUFFIXES = {".json", ".md", ".py", ".toml", ".yaml", ".yml"}
VERIFICATION_COMMAND = re.compile(r"(?:^|\s)(?:python(?:\.exe)?\s+-m\s+)?pytest(?:\s|$)", re.IGNORECASE)
MEMORY_SEARCH_COMMAND = re.compile(
    r"(?:dynamic_memory_cli\.py|memory-cli)\b[^\r\n]*\bsearch\b",
    re.IGNORECASE,
)


def build_tasks(project_root: Path) -> list[dict[str, Any]]:
    tasks: list[dict[str, Any]] = []
    for phase in PHASES:
        for component_index, component in enumerate(COMPONENTS):
            number = len(tasks) + 1
            if number == 1:
                instruction = (
                    "我想做一个小型的事件存储包，先从事件的基础格式开始。帮我把 canonical envelope 做出来，"
                    "代码放在 quarry/canonical_envelope.py。JSON 需要稳定、键递归排序并使用 UTF-8，文件结尾只留一个换行；"
                    "时间统一用带时区的 UTC，遇到坏数据直接报错。再补一组小而完整的测试，改完跑一下相关 pytest。"
                )
            elif phase == "contract":
                instruction = (
                    f"接着把 {component.name} 这块搭起来，代码放 quarry/{component.file}。我希望它做到："
                    f"{component.contract}。API 先小一点，核心逻辑和相关测试一起补上。"
                )
                if component.name in MEMORY_SEEDS:
                    instruction += " " + MEMORY_SEEDS[component.name]
            elif phase == "edge cases":
                instruction = (
                    f"{component.name} 这边有个小反馈：{EDGE_CASE_REPORTS[component.name]}。"
                    "现有 API 别动，修好后补个能防回归的测试。如果当前实现和回归测试已经完整覆盖，"
                    "请实际读取代码、运行相关测试并明确说明，无需为了产生 diff 重写正确代码。"
                )
            elif phase == "integration":
                previous = COMPONENTS[(component_index - 1) % len(COMPONENTS)]
                instruction = INTEGRATION_REPORTS.get(
                    component.name,
                    (
                        f"现在把 {component.name} 和 {previous.name} 串一下，尽量复用两边已有逻辑。"
                        "加一个真正跑起来的集成测试，把顺序和失败时的行为钉住。"
                    ),
                )
            elif phase == "observability":
                instruction = (
                    f"{component.name} 出问题时现在不太好查。加一份稳定的诊断结果，把成功和失败原因说清楚，"
                    "但别改变原来的返回语义；测试和一个短用法也一起补上。"
                )
            elif phase == "performance":
                instruction = PERFORMANCE_REPORTS.get(
                    component.name,
                    (
                        f"{component.name} 放到一万条事件时有点慢。看下是不是有明显重复工作，做一个范围小的优化，"
                        "结果必须和现在一致，再留个回归测试。"
                    ),
                )
            elif phase == "failure recovery":
                instruction = FAILURE_RECOVERY_REPORTS.get(
                    component.name,
                    (
                        f"再把 {component.name} 的崩溃恢复补扎实一点。找最危险的提交边界，用临时文件模拟一次中断，"
                        "恢复要能重复执行，而且已经确认的数据不能丢也不能多一份。"
                    ),
                )
            elif phase == "change request" and component.name in BLIND_CONFLICT_REQUESTS:
                instruction = BLIND_CONFLICT_REQUESTS[component.name]
            elif phase == "change request":
                instruction = CHANGE_REQUEST_REPORTS.get(
                    component.name,
                    (
                        f"给 {component.name} 加一个 v2 的批量用法吧。老调用方现在还在用，迁移要平滑；"
                        "适配层、测试和简短迁移说明一起处理。"
                    ),
                )
            else:
                if component.name == "audit exporter":
                    instruction = (
                        "准备收尾了，audit exporter 的批量失败边界还没钉实：补上后面的 journal 已关闭、"
                        "以及后面的 record 缺 time/id 这两个回归场景，确认整批明确报错且不会给调用方"
                        "留下可用的半批结果；如果测试揭出实现问题就做最小修复。相关测试跑通后，把命令、"
                        "结果、约束是怎么组合的以及还剩什么发布阻碍写到 release-evidence/audit.md。"
                    )
                elif component.name == "UTC clock":
                    instruction = (
                        "准备收尾了，UTC clock 的输入边界还没钉实：补上 lowercase z 和首尾空白这两类"
                        "非规范 timestamp 的回归场景，确认解析入口严格拒绝；再补一个带微秒、非整小时 offset"
                        " 的 canonical round-trip。若测试揭出实现问题就做最小修复。相关测试跑通后，把命令、"
                        "结果、约束是怎么组合的以及还剩什么发布阻碍写到 release-evidence/clock.md。"
                    )
                elif component.name == "release gate":
                    instruction = (
                        "准备收尾了，release gate 的批量契约还差一组发布前回归：让 evaluate_many 同时接收"
                        "外层 one-shot generator 和每组 tests_run 的内层 one-shot generator，补上空批次，"
                        "并确认 reproducibility check 每批只执行一次。若测试揭出实现问题就做最小修复。"
                        "相关测试跑通后，把命令、结果、约束是怎么组合的以及还剩什么发布阻碍写到 "
                        "release-evidence/release_gate.md。"
                    )
                else:
                    instruction = (
                        f"准备收尾了，帮我检查一下 {component.name}，找一个真实缺陷或测试空档修掉。"
                        f"相关测试跑通后，把命令、结果、约束是怎么组合的以及还剩什么发布阻碍写到 "
                        f"release-evidence/{Path(component.file).stem}.md。"
                    )
            if phase == "performance":
                instruction += (
                    "先用代码和可重复的基准确认瓶颈；如果证据表明确实没有明显重复工作且现有测试已覆盖，"
                    "允许把这一轮作为 verified no-op，但必须给出实际读取、基准和测试证据，不要制造 diff。"
                    "完成一次可重复基准和一次成功的全量 pytest 后，如果已经足够得出结论，立即给出简洁最终答复，"
                    "不要继续调用工具。"
                )
            tasks.append(
                {
                    "number": number,
                    "phase": phase,
                    "component": component.name,
                    "conflict_probe": component.name in BLIND_CONFLICT_REQUESTS and phase == "change request",
                    "probe_marker": PROBE_MARKERS.get(component.name) if phase == "change request" else None,
                    "allow_verified_noop": phase in {"edge cases", "performance"},
                    "prompt": instruction,
                }
            )
    assert len(tasks) == 200
    return tasks


def initialize_project(project_root: Path) -> None:
    (project_root / "quarry").mkdir(parents=True, exist_ok=True)
    (project_root / "tests").mkdir(parents=True, exist_ok=True)
    (project_root / "quarry" / "__init__.py").write_text(
        '"""Quarry durable-workflow acceptance project."""\n', encoding="utf-8"
    )
    (project_root / "pyproject.toml").write_text(
        "[project]\n"
        'name = "quarry-eternal-acceptance"\n'
        'version = "0.1.0"\n'
        'requires-python = ">=3.11"\n\n'
        "[tool.pytest.ini_options]\n"
        'testpaths = ["tests"]\n'
        'addopts = "-q"\n',
        encoding="utf-8",
    )
    (project_root / "README.md").write_text(
        "# Quarry\n\n"
        "A small durable-workflow package that is being evolved through normal development requests.\n",
        encoding="utf-8",
    )


def project_manifest(project_root: Path) -> dict[str, str]:
    manifest: dict[str, str] = {}
    for path in sorted(project_root.rglob("*")):
        if not path.is_file() or any(part in IGNORED_PROJECT_PARTS for part in path.relative_to(project_root).parts):
            continue
        if path.suffix.casefold() not in TRACKED_PROJECT_SUFFIXES:
            continue
        manifest[path.relative_to(project_root).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    return manifest


def changed_project_paths(before: dict[str, str], after: dict[str, str]) -> list[str]:
    return sorted(path for path in set(before) | set(after) if before.get(path) != after.get(path))


def capture_project_baseline(project_root: Path) -> dict[str, bytes]:
    return {
        path: (project_root / path).read_bytes()
        for path in project_manifest(project_root)
    }


def restore_project_baseline(project_root: Path, baseline: dict[str, bytes]) -> None:
    current_paths = set(project_manifest(project_root))
    for relative in current_paths - set(baseline):
        (project_root / relative).unlink()
    for relative, content in baseline.items():
        target = project_root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    text = read_jsonl_snapshot(path)
    lines = text.splitlines()
    rows: list[dict[str, Any]] = []
    for index, line in enumerate(lines):
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            # Event stores append JSONL concurrently and a terminated process
            # can leave its final record truncated either before or after a
            # newline was flushed.  Only the final physical record is allowed
            # to be unpublished; corruption anywhere earlier remains fatal.
            if index == len(lines) - 1:
                break
            raise
    return rows


def consecutive_passed_prefix(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    prefix: list[dict[str, Any]] = []
    for row in rows:
        expected_number = len(prefix) + 1
        if row.get("status") != "passed" or int(row.get("number") or 0) != expected_number:
            break
        prefix.append(row)
    return prefix


@contextmanager
def exclusive_run_lock(run_root: Path):
    """Hold a process-level lock so one acceptance run has one writer."""

    path = run_root / ".acceptance.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("a+b")
    if path.stat().st_size == 0:
        handle.write(b"0")
        handle.flush()
    handle.seek(0)
    try:
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as exc:
        handle.close()
        raise RuntimeError(f"acceptance run already has an active writer: {run_root}") from exc
    try:
        yield
    finally:
        handle.seek(0)
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()


def task_tool_evidence(run_dir: Path) -> dict[str, Any]:
    calls = load_jsonl(run_dir / "tool_calls.jsonl")
    results = load_jsonl(run_dir / "tool_results.jsonl")
    successful_results = {
        str(row.get("tool_call_id")): row
        for row in results
        if row.get("status") == "success" and row.get("tool_call_id")
    }
    successful_ids = set(successful_results)
    successful_calls = [row for row in calls if str(row.get("event_id")) in successful_ids]
    verification_calls = []
    memory_search_calls = []
    for row in successful_calls:
        command = str((row.get("args") or {}).get("command") or "")
        result = str(successful_results[str(row.get("event_id"))].get("result") or "")
        if row.get("tool_name") == "bash" and MEMORY_SEARCH_COMMAND.search(command):
            memory_search_calls.append(row)
        if (
            row.get("tool_name") == "bash"
            and VERIFICATION_COMMAND.search(command)
            and "--version" not in command.casefold()
            and result.lstrip().startswith("exit_code: 0")
            and (re.search(r"\b\d+ passed\b", result, re.IGNORECASE) or "[100%]" in result)
            and not re.search(r"\b[1-9]\d* (?:failed|errors?)\b", result, re.IGNORECASE)
        ):
            verification_calls.append(row)
    return {
        "tool_call_count": len(calls),
        "successful_tool_call_count": len(successful_calls),
        "tool_names": sorted({str(row.get("tool_name")) for row in successful_calls}),
        "verification_commands": [str((row.get("args") or {}).get("command") or "") for row in verification_calls],
        "has_successful_verification": bool(verification_calls),
        "memory_search_commands": [
            str((row.get("args") or {}).get("command") or "") for row in memory_search_calls
        ],
        "memory_search_count": len(memory_search_calls),
    }


def verified_noop_allowed(
    task: dict[str, Any],
    *,
    changed_paths: list[str],
    tool_evidence: dict[str, Any],
) -> bool:
    """Accept a verified edge-case request that the existing code already satisfies."""

    return bool(
        task.get("allow_verified_noop") is True
        and not changed_paths
        and int(tool_evidence.get("successful_tool_call_count") or 0) >= 2
        and tool_evidence.get("has_successful_verification")
        and "read" in set(tool_evidence.get("tool_names") or ())
    )


def mark_run_crashed(run_root: Path, error: BaseException) -> None:
    path = run_root / "run.json"
    if not path.exists():
        return
    metadata = read_json(path)
    metadata.update(
        {
            "finished_at": utc_now(),
            "status": "failed",
            "failure": f"{type(error).__name__}: {error}",
        }
    )
    write_json_atomic(path, metadata)


async def recover_memory_backlog(
    runtime: LoraRuntimeService,
    manager: SessionManager,
    session_ref: SessionRef,
    *,
    max_uncovered_messages: int,
    recovery_attempts: int,
) -> dict[str, Any]:
    """Boundedly resume only the failed background memory job until caught up."""

    session_dir = Path(session_ref.session_dir)
    for recovery_attempt in range(recovery_attempts + 1):
        await runtime.memory_harness.wait_idle()
        session = manager.load(session_ref.session_id)
        projection = load_projection(session_dir)
        uncovered = len(session.history) - int(projection.get("covered_through") or 0)
        if uncovered <= max_uncovered_messages:
            return projection
        if recovery_attempt == recovery_attempts:
            raise RuntimeError(
                "memory projection remains behind after "
                f"{recovery_attempts} background recovery attempts: "
                f"uncovered={uncovered}, allowed={max_uncovered_messages}"
            )
        scheduled = await runtime.memory_harness.retry_pending(session)
        if not scheduled:
            raise RuntimeError(
                f"memory projection is behind by {uncovered} messages but no retry was scheduled"
            )
    raise AssertionError("unreachable memory recovery state")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


SESSION_ROLLBACK_FILES = (
    "session.json",
    "context/history.jsonl",
    "context/conversation-checkpoints.sqlite3",
    "context/conversation-checkpoints.sqlite3-wal",
    "context/conversation-checkpoints.sqlite3-shm",
    "raw-history/events.jsonl",
    "state/eternal-conversation.json",
    "state/eternal-harness.json",
    "memory/memory.sqlite3",
    "memory/memory.sqlite3-wal",
    "memory/memory.sqlite3-shm",
)


def capture_session_baseline(session_dir: Path) -> dict[str, bytes | None]:
    return {
        relative: (path.read_bytes() if path.is_file() else None)
        for relative in SESSION_ROLLBACK_FILES
        for path in (session_dir / relative,)
    }


def restore_session_baseline(session_dir: Path, baseline: dict[str, bytes | None]) -> None:
    for relative, content in baseline.items():
        path = session_dir / relative
        if content is None:
            path.unlink(missing_ok=True)
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)


ACTIVE_TASK_BASELINE = "active-task-baseline.zip"


def persist_task_baseline(
    run_root: Path,
    *,
    task_number: int,
    project_baseline: dict[str, bytes],
    session_baseline: dict[str, bytes | None],
) -> None:
    """Atomically persist the rollback boundary for the currently active task."""

    target = run_root / ACTIVE_TASK_BASELINE
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.{os.getpid()}.tmp")
    metadata = {
        "task_number": task_number,
        "project_paths": sorted(project_baseline),
        "session_paths": {
            relative: content is not None for relative, content in session_baseline.items()
        },
    }
    try:
        with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("metadata.json", json.dumps(metadata, ensure_ascii=False, sort_keys=True))
            for relative, content in project_baseline.items():
                archive.writestr(f"project/{relative}", content)
            for relative, content in session_baseline.items():
                if content is not None:
                    archive.writestr(f"session/{relative}", content)
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)


def restore_active_task_baseline(
    run_root: Path,
    *,
    expected_task_number: int,
    project_root: Path,
    session_dir: Path,
) -> bool:
    """Restore an interrupted task before resuming its acceptance run."""

    path = run_root / ACTIVE_TASK_BASELINE
    if not path.exists():
        return False
    stale_committed_baseline = False
    with zipfile.ZipFile(path, "r") as archive:
        metadata = json.loads(archive.read("metadata.json"))
        task_number = int(metadata["task_number"])
        if task_number < expected_task_number:
            stale_committed_baseline = True
        elif task_number != expected_task_number:
            raise RuntimeError(
                f"active task baseline is for task {task_number}, expected {expected_task_number}"
            )
        else:
            project_paths = set(metadata["project_paths"])
            current_paths = set(capture_project_baseline(project_root))
            for relative in current_paths - project_paths:
                (project_root / relative).unlink()
            for relative in project_paths:
                target = project_root / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(archive.read(f"project/{relative}"))
            for relative, exists in metadata["session_paths"].items():
                target = session_dir / relative
                if not exists:
                    target.unlink(missing_ok=True)
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(archive.read(f"session/{relative}"))
    path.unlink()
    return not stale_committed_baseline


def clear_active_task_baseline(run_root: Path) -> None:
    (run_root / ACTIVE_TASK_BASELINE).unlink(missing_ok=True)


def question_evidence(answer: str) -> bool:
    text = answer.strip()
    if not text:
        return False
    # A clarification must be the foreground's final hand-off, not a question
    # buried in chain-of-thought-like analysis that never actually yields.
    return bool(re.search(r"[?？][\s*_`'\"）)\]]*$", text))


def build_retry_message(task_prompt: str) -> str:
    return (
        "刚才这件事没有完成，工作区和会话已经恢复到尝试前。请重新独立处理下面这件事，"
        "先核对相关实现和既有约束，不要沿用上一轮的推断。Raw History 会保留失败尝试的"
        "审计证据，其中的完成声明不代表当前工作区状态；请以当前文件为准完成任务。"
        "如果任务点名的 helper 或公共 API 尚不存在，但已经说明了所需行为，补充最小兼容实现"
        "属于本任务范围，不要仅因为符号缺失而反问。"
        "这是实现任务：必须使用工具把范围内的改动持久化并运行相关验证，不要用设计说明代替执行。"
        "除非原任务明确允许 verified no-op，否则检查后必须选择满足要求的最小可靠改动，不能以当前实现已经很快或足够好为由无改动结束。"
        "一旦已有足够的改动、基准和测试证据，立即给出简洁最终答复，不要在没有具体剩余检查时继续调用工具。"
        "若原任务明确允许 verified no-op 且当前实现和测试已完整满足要求，请核验证据后明确说明，不要制造无意义改动：\n\n"
        + task_prompt
    )


def probe_memory_evidence(answer: str, marker: str | None) -> bool:
    return bool(marker and marker.casefold() in answer.casefold())


def fresh_session_evidence(manager: SessionManager, session_ref: SessionRef) -> dict[str, Any]:
    session_dir = Path(session_ref.session_dir)
    session = manager.load(session_ref.session_id)
    projection = load_projection(session_dir)
    evidence = {
        "history_messages": len(session.history),
        "memory_database_exists": (session_dir / "memory" / "memory.sqlite3").exists(),
        "raw_history_exists": (session_dir / "raw-history" / "events.jsonl").exists(),
        "memory_revision": int(projection.get("memory_revision") or 0),
        "snapshot_revision": int(projection.get("snapshot_revision") or 0),
        "covered_through": int(projection.get("covered_through") or 0),
    }
    evidence["clean"] = not any(
        (
            evidence["history_messages"],
            evidence["memory_database_exists"],
            evidence["raw_history_exists"],
            evidence["memory_revision"],
            evidence["snapshot_revision"],
            evidence["covered_through"],
        )
    )
    return evidence


async def run(args: argparse.Namespace) -> int:
    workspace = Path(args.workspace).resolve()
    interpreter_bin = str(Path(sys.executable).resolve().parent)
    path_entries = os.environ.get("PATH", "").split(os.pathsep)
    if os.path.normcase(interpreter_bin) not in {
        os.path.normcase(str(Path(entry).resolve())) for entry in path_entries if entry
    }:
        os.environ["PATH"] = os.pathsep.join((interpreter_bin, *path_entries))
    config = load_run_config(
        workspace_root=str(workspace),
        agent_alias=args.agent_alias,
        max_steps=args.max_steps,
    )
    if not config.eternal_conversation.enabled:
        raise RuntimeError("eternal_conversation must be enabled")
    if args.foreground_model:
        assert config.resolved_agent is not None
        for route in config.resolved_agent.routes:
            route.model_name = args.foreground_model

    resume_rows: list[dict[str, Any]] = []
    fresh_history: dict[str, Any] | None = None
    if args.resume_run:
        run_root = Path(args.resume_run).resolve()
        run_meta = read_json(run_root / "run.json")
        project_root = Path(run_meta["project_root"]).resolve()
    else:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        run_root = Path(config.lora_root) / "acceptance-runs" / f"eternal-blind-200-{stamp}"
        project_root = workspace / "workspace" / f"eternal-blind-acceptance-200-{stamp}"
        project_root.mkdir(parents=True, exist_ok=False)
        (project_root / ".acceptance-root").write_text(
            "Fresh Quarry blind eternal-conversation acceptance project root.\n",
            encoding="utf-8",
        )
        initialize_project(project_root)
        # Foreground and background Agents resolve the same user-level Lora
        # configuration independently for this disposable workspace. Never copy
        # user configuration or credentials into the acceptance project.
    # Keep the fresh Session inside the disposable project so the foreground can
    # follow the advertised Raw History path without gaining read access to the
    # real repository or any older acceptance Session.
    config.workspace_root = str(project_root)
    config.lora_root = str(project_root / ".lora")
    config.allow_read_outside_workspace = False
    runtime_root = project_root / ".lora" / "runtime"
    config.runtime_durability.history_path = str(runtime_root / "executions.sqlite3")
    config.runtime_capacity.coordinator_path = str(runtime_root / "capacity.sqlite3")
    tasks = build_tasks(project_root)[: args.limit]

    manager = SessionManager(config)
    if args.resume_run:
        run_meta = read_json(run_root / "run.json")
        session_ref = SessionRef(
            session_id=str(run_meta["session_id"]),
            session_dir=str(run_meta["session_dir"]),
            workspace_root=str(project_root),
        )
        all_previous_rows = load_jsonl(run_root / "progress.jsonl")
        resume_rows = consecutive_passed_prefix(all_previous_rows)
        restore_active_task_baseline(
            run_root,
            expected_task_number=len(resume_rows) + 1,
            project_root=project_root,
            session_dir=Path(session_ref.session_dir),
        )
        interrupted_rows = all_previous_rows[len(resume_rows) :]
        for row in interrupted_rows:
            append_jsonl(run_root / "interrupted-tasks.jsonl", row)
        (run_root / "progress.jsonl").write_text(
            "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in resume_rows),
            encoding="utf-8",
        )
        # Keep the evidence manifest aligned with the prompts actually used
        # after a resumable acceptance scenario is refined.
        write_json(run_root / "tasks.json", {"tasks": tasks})
    else:
        write_json(run_root / "tasks.json", {"tasks": tasks})
        session_ref = manager.create("eternal-blind-200", mode="chat")
        fresh_history = fresh_session_evidence(manager, session_ref)
        if not fresh_history["clean"]:
            raise RuntimeError(f"blind acceptance session was not empty: {fresh_history}")
    runtime = LoraRuntimeService(config)
    await runtime.initialize()
    if args.resume_run:
        run_meta = read_json(run_root / "run.json")
        run_meta.update(
            {
                "status": "running",
                "failure": None,
                "resumed_at": utc_now(),
                "resume_count": int(run_meta.get("resume_count") or 0) + 1,
                "max_steps": config.max_steps,
                "task_attempts": args.task_attempts,
                "memory_recovery_attempts": args.memory_recovery_attempts,
            }
        )
        write_json(run_root / "run.json", run_meta)
    else:
        assert fresh_history is not None
        write_json(
            run_root / "run.json",
            {
            "started_at": utc_now(),
            "status": "running",
            "session_id": session_ref.session_id,
            "session_dir": session_ref.session_dir,
            "project_root": str(project_root),
            "foreground_alias": config.resolved_agent.alias if config.resolved_agent else None,
            "foreground_model": (
                config.resolved_agent.routes[0].model_name
                if config.resolved_agent and config.resolved_agent.routes
                else None
            ),
            "extractor_alias": config.eternal_conversation.extractor_agent_alias,
            "builder_alias": config.eternal_conversation.builder_agent_alias,
            "max_steps": config.max_steps,
            "task_attempts": args.task_attempts,
            "memory_recovery_attempts": args.memory_recovery_attempts,
            "fresh_history": fresh_history,
            "scenario": "conversational-blind-memory",
            },
        )

    passed = len(resume_rows)
    failure: str | None = None
    try:
        for task in tasks[len(resume_rows) :]:
            index = int(task["number"])
            session_dir = Path(session_ref.session_dir)
            try:
                await recover_memory_backlog(
                    runtime,
                    manager,
                    session_ref,
                    max_uncovered_messages=args.max_uncovered_messages,
                    recovery_attempts=args.memory_recovery_attempts,
                )
            except Exception as exc:
                failure = f"memory recovery before task {index} failed: {type(exc).__name__}: {exc}"
                break
            before = load_projection(session_dir)
            manifest_before = project_manifest(project_root)
            task_baseline = capture_project_baseline(project_root)
            session_baseline = capture_session_baseline(session_dir)
            persist_task_baseline(
                run_root,
                task_number=index,
                project_baseline=task_baseline,
                session_baseline=session_baseline,
            )
            started = time.monotonic()
            status = "error"
            answer = ""
            error: str | None = None
            attempts: list[dict[str, Any]] = []
            tool_evidence = {
                "tool_call_count": 0,
                "successful_tool_call_count": 0,
                "tool_names": [],
                "verification_commands": [],
                "has_successful_verification": False,
                "memory_search_commands": [],
                "memory_search_count": 0,
            }
            changed_paths: list[str] = []
            gate_failures: list[str] = []
            verified_noop = False
            for attempt in range(1, args.task_attempts + 1):
                run_ref = manager.start_case_run(
                    session_ref.session_id,
                    f"acceptance-{index:03d}-attempt-{attempt}",
                    run_config=config,
                )
                attempt_status = "error"
                attempt_error: str | None = None
                attempt_traceback: str | None = None
                if attempt == 1:
                    message = str(task["prompt"])
                else:
                    message = build_retry_message(str(task["prompt"]))
                try:
                    handle = await runtime.start_turn(
                        manager=manager,
                        message=message,
                        run_ref=run_ref,
                        turn_id=f"turn-{index:04d}-attempt-{attempt}",
                        interactive_approvals=False,
                        deadline=time.monotonic() + args.turn_timeout,
                    )
                    output, _ = await handle.result()
                    output_data = thaw_json(output.data)
                    result = (
                        dict(output_data.get("result") or {})
                        if isinstance(output_data, dict)
                        else {}
                    )
                    attempt_status = str(result.get("status") or "error")
                    answer = str(result.get("final_answer") or "")
                    attempt_error = result.get("error")
                except Exception as exc:  # retain exact failed-attempt evidence
                    attempt_traceback = traceback.format_exc()
                    kind = getattr(exc, "kind", None)
                    attempt_error = f"{type(exc).__name__}: {exc}"
                    if kind is not None:
                        attempt_error += f" (kind={getattr(kind, 'value', kind)})"

                attempt_tools = task_tool_evidence(Path(run_ref.run_dir))
                tool_evidence["tool_call_count"] += attempt_tools["tool_call_count"]
                tool_evidence["successful_tool_call_count"] += attempt_tools["successful_tool_call_count"]
                tool_evidence["tool_names"] = sorted(
                    set(tool_evidence["tool_names"]) | set(attempt_tools["tool_names"])
                )
                tool_evidence["verification_commands"].extend(attempt_tools["verification_commands"])
                tool_evidence["memory_search_commands"].extend(attempt_tools["memory_search_commands"])
                tool_evidence["memory_search_count"] += attempt_tools["memory_search_count"]
                tool_evidence["has_successful_verification"] = bool(
                    tool_evidence["has_successful_verification"] or attempt_tools["has_successful_verification"]
                )
                changed_paths = changed_project_paths(manifest_before, project_manifest(project_root))
                verified_noop = verified_noop_allowed(
                    task,
                    changed_paths=changed_paths,
                    tool_evidence=tool_evidence,
                )
                gate_failures = []
                if tool_evidence["successful_tool_call_count"] < 2:
                    gate_failures.append("fewer than two successful real tool calls")
                if not task["conflict_probe"] and not tool_evidence["has_successful_verification"]:
                    gate_failures.append("no successful pytest command executed through the bash tool")
                if task["conflict_probe"]:
                    if changed_paths:
                        gate_failures.append("conflict probe modified persistent project files")
                    if not question_evidence(answer):
                        gate_failures.append("conflict probe did not ask a natural clarification question")
                    if not probe_memory_evidence(answer, task.get("probe_marker")):
                        gate_failures.append("conflict probe did not recover the hidden conversational marker")
                elif not changed_paths and not verified_noop:
                    gate_failures.append("no persistent project source, test, or evidence file changed")
                status = attempt_status
                error = attempt_error
                if status == "passed" and gate_failures:
                    status = "failed"
                    error = "; ".join(gate_failures)
                if status == "passed":
                    try:
                        await runtime.memory_harness.wait_idle()
                    except Exception as exc:
                        status = "error"
                        error = (
                            "eternal-conversation background attempt failed: "
                            f"{type(exc).__name__}: {exc}"
                        )
                manager.finish_case_run(
                    run_ref,
                    status if status in {"passed", "failed", "error", "skipped"} else "error",
                )
                attempts.append(
                    {
                        "attempt": attempt,
                        "case_run_id": run_ref.case_run_id,
                        "status": status,
                        "error": error,
                        "traceback": attempt_traceback,
                        "tool_evidence": attempt_tools,
                        "changed_project_paths": changed_paths,
                        "verified_noop": verified_noop,
                    }
                )
                append_jsonl(
                    run_root / "attempts-live.jsonl",
                    {
                        "recorded_at": utc_now(),
                        "task_number": index,
                        "attempt": attempt,
                        "case_run_id": run_ref.case_run_id,
                        "status": status,
                        "error": error,
                        "traceback": attempt_traceback,
                        "gate_failures": gate_failures,
                        "changed_project_paths": changed_paths,
                        "verified_noop": verified_noop,
                        "tool_evidence": attempt_tools,
                    },
                )
                if status == "passed":
                    break
                try:
                    await runtime.memory_harness.wait_idle()
                except Exception:
                    # A failed background task was already captured in this attempt's
                    # status. Cleanup must still reach the transactional rollback.
                    pass
                restore_project_baseline(project_root, task_baseline)
                restore_session_baseline(session_dir, session_baseline)

            session_after = manager.load(session_ref.session_id)
            after = load_projection(session_dir)
            barrier_waited = False
            uncovered = len(session_after.history) - int(after.get("covered_through") or 0)
            if status == "passed" and uncovered > args.max_uncovered_messages:
                barrier_waited = True
                try:
                    after = await recover_memory_backlog(
                        runtime,
                        manager,
                        session_ref,
                        max_uncovered_messages=args.max_uncovered_messages,
                        recovery_attempts=args.memory_recovery_attempts,
                    )
                except Exception as exc:
                    status = "error"
                    error = f"background memory recovery failed: {type(exc).__name__}: {exc}"
                    after = load_projection(session_dir)
            record = {
                **task,
                "started_at": utc_now(),
                "duration_seconds": round(time.monotonic() - started, 3),
                "status": status,
                "error": error,
                "answer": answer,
                "question_evidence": question_evidence(answer),
                "probe_memory_evidence": probe_memory_evidence(answer, task.get("probe_marker")),
                "tool_evidence": tool_evidence,
                "changed_project_paths": changed_paths,
                "verified_noop": verified_noop,
                "execution_gate_failures": gate_failures,
                "attempts": attempts,
                "memory_barrier_waited": barrier_waited,
                "projection_before": {
                    key: before.get(key) for key in ("memory_revision", "snapshot_revision", "covered_through")
                },
                "projection_after": {
                    key: after.get(key) for key in ("memory_revision", "snapshot_revision", "covered_through")
                },
                "history_messages": len(session_after.history),
            }
            append_jsonl(run_root / "progress.jsonl", record)
            if status == "passed":
                clear_active_task_baseline(run_root)
            heartbeat = {
                "updated_at": utc_now(),
                "completed": index,
                "passed": passed + (1 if status == "passed" else 0),
                "last_status": status,
                "last_component": task["component"],
                "tool_calls": tool_evidence["tool_call_count"],
                "changed_files": len(changed_paths),
                "snapshot_revision": after.get("snapshot_revision", 0),
                "covered_through": after.get("covered_through", 0),
            }
            write_json(
                run_root / "heartbeat.json",
                heartbeat,
            )
            print(json.dumps({"heartbeat": heartbeat}, ensure_ascii=False), flush=True)
            if status != "passed":
                failure = f"task {index} failed: {error or status}"
                break
            passed += 1
        if failure is None:
            try:
                await recover_memory_backlog(
                    runtime,
                    manager,
                    session_ref,
                    max_uncovered_messages=0,
                    recovery_attempts=args.memory_recovery_attempts,
                )
            except Exception as exc:
                failure = f"final memory recovery failed: {type(exc).__name__}: {exc}"
    except BaseException as exc:
        mark_run_crashed(run_root, exc)
        raise
    finally:
        await runtime.close(cancel=False)

    session_dir = Path(session_ref.session_dir)
    projection = load_projection(session_dir)
    rows = [json.loads(line) for line in (run_root / "progress.jsonl").read_text(encoding="utf-8").splitlines()]
    conflict_rows = [row for row in rows if row.get("conflict_probe")]
    memory_db = session_dir / "memory" / "memory.sqlite3"
    states: dict[str, int] = {}
    if memory_db.exists():
        with sqlite3.connect(memory_db) as connection:
            states = dict(connection.execute("SELECT build_state,COUNT(*) FROM uts GROUP BY build_state").fetchall())
    successful_by_component = {
        str(row["component"]): row
        for row in conflict_rows
        if question_evidence(str(row.get("answer") or ""))
        and probe_memory_evidence(str(row.get("answer") or ""), row.get("probe_marker"))
    }
    supplemental_path = run_root / "supplemental-compat-probe.json"
    supplemental = read_json(supplemental_path) if supplemental_path.exists() else {}
    if (
        supplemental.get("accepted")
        and supplemental.get("component") == "compatibility facade"
        and question_evidence(str(supplemental.get("answer") or ""))
        and probe_memory_evidence(str(supplemental.get("answer") or ""), "0.4")
    ):
        successful_by_component["compatibility facade"] = supplemental
    successful_conflicts = list(successful_by_component.values())
    real_tool_rows = [row for row in rows if int((row.get("tool_evidence") or {}).get("successful_tool_call_count") or 0) >= 2]
    verified_rows = [row for row in rows if (row.get("tool_evidence") or {}).get("has_successful_verification")]
    changed_rows = [row for row in rows if row.get("changed_project_paths")]
    verified_noop_rows = [row for row in rows if row.get("verified_noop")]
    clean_conflicts = [row for row in conflict_rows if not row.get("changed_project_paths")]
    memory_search_rows = [
        row for row in rows if int((row.get("tool_evidence") or {}).get("memory_search_count") or 0) > 0
    ]
    memory_search_count = sum(
        int((row.get("tool_evidence") or {}).get("memory_search_count") or 0) for row in rows
    )
    conflict_memory_search_rows = [row for row in conflict_rows if row in memory_search_rows]
    full_suite_path = run_root / "final-project-pytest.txt"
    full_suite = subprocess.run(
        [sys.executable, "-m", "pytest", "-q"],
        cwd=project_root,
        text=True,
        capture_output=True,
        timeout=args.final_test_timeout,
        check=False,
    )
    full_suite_path.write_text(
        f"exit_code={full_suite.returncode}\n\nSTDOUT\n{full_suite.stdout}\n\nSTDERR\n{full_suite.stderr}\n",
        encoding="utf-8",
    )
    proof = {
        "completed_tasks": len(rows),
        "passed_tasks": passed,
        "required_tasks": 200,
        "final_projection": {
            key: projection.get(key) for key in ("memory_revision", "snapshot_revision", "covered_through")
        },
        "ut_states": states,
        "conflict_probes": len(conflict_rows),
        "blind_memory_probes": len(conflict_rows),
        "blind_memory_probes_passed": len(successful_conflicts),
        "supplemental_probe_rechecks": 1 if supplemental.get("accepted") else 0,
        "tasks_with_real_tools": len(real_tool_rows),
        "tasks_with_successful_pytest": len(verified_rows),
        "tasks_with_persistent_changes": len(changed_rows),
        "tasks_verified_as_already_satisfied": len(verified_noop_rows),
        "clean_conflict_probes": len(clean_conflicts),
        "foreground_memory_searches": memory_search_count,
        "tasks_using_memory_search": len(memory_search_rows),
        "blind_probes_using_memory_search": len(conflict_memory_search_rows),
        "previous_acceptance_memory_searches": 1,
        "final_project_pytest_exit_code": full_suite.returncode,
        "final_project_pytest_output": str(full_suite_path),
        "project_root": str(project_root),
        "raw_history": str(session_dir / "raw-history" / "events.jsonl"),
        "foreground_history": str(session_dir / "agent-history" / "foreground" / "conversation.jsonl"),
        "extractor_history": str(session_dir / "agent-history" / "extractor" / "conversation.jsonl"),
        "builder_history": str(session_dir / "agent-history" / "builder" / "conversation.jsonl"),
        "session_history": str(session_dir / "session.json"),
    }
    accepted = (
        failure is None
        and passed == 200
        and int(projection.get("snapshot_revision") or 0) >= 2
        and int(projection.get("covered_through") or 0) > 0
        and int(projection.get("covered_through") or 0) == len(manager.load(session_ref.session_id).history)
        and states.get("pending", 0) == 0
        and len(successful_conflicts) == len(BLIND_CONFLICT_REQUESTS)
        and len(real_tool_rows) == 200
        and len(verified_rows) == 200 - len(BLIND_CONFLICT_REQUESTS)
        and len(changed_rows) + len(verified_noop_rows) == 200 - len(BLIND_CONFLICT_REQUESTS)
        and len(clean_conflicts) == len(BLIND_CONFLICT_REQUESTS)
        and full_suite.returncode == 0
    )
    write_json(run_root / "proof.json", {**proof, "accepted": accepted})
    excerpts = "\n\n".join(
        f"### Task {row['number']}: {row['component']}\n\n{_proof_answer_excerpt(row['answer'])}"
        for row in successful_conflicts
    )
    (run_root / "PROOF.md").write_text(
        "# Conversational Blind Eternal Conversation Acceptance Proof\n\n"
        f"- Result: {'PASS' if accepted else 'FAIL'}\n"
        f"- Passed tasks: {passed}/200\n"
        f"- Session: `{session_ref.session_id}`\n"
        f"- Final Snapshot revision: {projection.get('snapshot_revision', 0)}\n"
        f"- Final covered cursor: {projection.get('covered_through', 0)}\n"
        f"- Built/Pending UTs: {states.get('built', 0)}/{states.get('pending', 0)}\n"
        f"- Hidden conversational memory probes: {len(successful_conflicts)}/{len(BLIND_CONFLICT_REQUESTS)}\n"
        f"- Supplemental strict probe rechecks: {1 if supplemental.get('accepted') else 0}\n"
        f"- Foreground memory-cli searches: {memory_search_count} (previous acceptance: 1)\n"
        f"- Tasks using memory-cli search: {len(memory_search_rows)}/200\n"
        f"- Blind probes using memory-cli search: {len(conflict_memory_search_rows)}/{len(BLIND_CONFLICT_REQUESTS)}\n"
        f"- Tasks with real successful tools: {len(real_tool_rows)}/200\n"
        f"- Non-probe tasks with successful foreground pytest: {len(verified_rows)}/{200 - len(BLIND_CONFLICT_REQUESTS)}\n"
        f"- Non-probe tasks with persistent changes: {len(changed_rows)}/{200 - len(BLIND_CONFLICT_REQUESTS)}\n"
        f"- Non-probe tasks verified as already satisfied: {len(verified_noop_rows)}\n"
        f"- Blind probes without mutation: {len(clean_conflicts)}/{len(BLIND_CONFLICT_REQUESTS)}\n"
        f"- Final full-project pytest: {'PASS' if full_suite.returncode == 0 else 'FAIL'}\n"
        f"- Failure: {failure or 'none'}\n\n"
        "## Preserved histories\n\n"
        + "\n".join(f"- {key}: `{value}`" for key, value in proof.items() if key.endswith("history"))
        + "\n\n## Hidden-memory probe excerpts\n\n"
        + (excerpts or "No successful conflict evidence.")
        + "\n",
        encoding="utf-8",
    )
    run_meta = read_json(run_root / "run.json")
    run_meta.update({"finished_at": utc_now(), "status": "passed" if accepted else "failed", "failure": failure})
    write_json(run_root / "run.json", run_meta)
    print(json.dumps({"run_root": str(run_root), "session_id": session_ref.session_id, **proof, "accepted": accepted}, ensure_ascii=False))
    return 0 if accepted else 1


def _proof_answer_excerpt(answer: str, *, limit: int = 4000) -> str:
    if len(answer) <= limit:
        return answer
    return "[... earlier analysis omitted ...]\n\n" + answer[-limit:]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", default=str(Path(__file__).resolve().parents[2]))
    parser.add_argument("--agent-alias", default=None)
    parser.add_argument("--foreground-model", default=None)
    parser.add_argument("--turn-timeout", type=float, default=15 * 60)
    parser.add_argument("--max-uncovered-messages", type=int, default=20)
    parser.add_argument("--memory-recovery-attempts", type=int, default=3)
    parser.add_argument("--max-steps", type=int, default=DEFAULT_REACT_MAX_STEPS)
    parser.add_argument("--task-attempts", type=int, default=12)
    parser.add_argument("--final-test-timeout", type=float, default=30 * 60)
    parser.add_argument("--limit", type=int, default=200, choices=range(1, 201))
    parser.add_argument("--resume-run", default=None)
    args = parser.parse_args()
    if args.resume_run:
        with exclusive_run_lock(Path(args.resume_run).resolve()):
            return asyncio.run(run(args))
    return asyncio.run(run(args))


if __name__ == "__main__":
    raise SystemExit(main())
