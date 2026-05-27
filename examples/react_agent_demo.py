r"""
Simple pygent ReAct agent demo using DeepSeek.

Run from the repository root:

    .\.venv\Scripts\python.exe examples\react_agent_demo.py

The demo loads DEEPSEEK_API_KEY from .env, verifies the registered tools,
then asks DeepSeek to solve a tiny task by calling those tools.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any, AsyncIterator, Iterable, Union


def _ensure_local_pygent_importable() -> None:
    """Allow this demo to run before pygent-ai is installed into .venv."""
    try:
        import pygent  # noqa: F401
        return
    except ModuleNotFoundError:
        pass

    local_checkout = Path(__file__).resolve().parents[2] / "pygent"
    if local_checkout.exists():
        sys.path.insert(0, str(local_checkout))


def _load_env_file(path: Path) -> None:
    """Small .env loader so the demo does not require python-dotenv."""
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


_ensure_local_pygent_importable()

from pygent.agent import BaseAgent
from pygent.context import BaseContext
from pygent.llm import AsyncRequestsClient
from pygent.message import BaseMessageChunk, ToolMessage, UserMessage
from pygent.module.tool import BaseTool, ToolCategory, ToolManager


class ListFilesTool(BaseTool):
    def __init__(self, workspace_root: Path):
        super().__init__(
            name="list_files",
            description="List files and directories under a relative workspace path.",
            category=ToolCategory.FILE,
        )
        self.workspace_root = workspace_root
        self.parameters.data["relative_path"]["description"] = "Relative directory path inside the workspace."

    def forward(self, relative_path: str = ".") -> dict[str, Any]:
        target = self._safe_path(relative_path)
        if not target.exists():
            raise FileNotFoundError(f"{relative_path!r} does not exist")
        if not target.is_dir():
            raise NotADirectoryError(f"{relative_path!r} is not a directory")
        entries = []
        for child in sorted(target.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())):
            entries.append({"name": child.name, "type": "dir" if child.is_dir() else "file"})
        return {"path": str(target.relative_to(self.workspace_root)), "entries": entries}

    def _safe_path(self, relative_path: str) -> Path:
        target = (self.workspace_root / relative_path).resolve()
        if target != self.workspace_root and self.workspace_root not in target.parents:
            raise ValueError("Path escapes the workspace root")
        return target


class ReadTextFileTool(BaseTool):
    def __init__(self, workspace_root: Path):
        super().__init__(
            name="read_text_file",
            description="Read a UTF-8 text file from the workspace.",
            category=ToolCategory.FILE,
        )
        self.workspace_root = workspace_root
        self.parameters.data["relative_path"]["description"] = "Relative file path inside the workspace."
        self.parameters.data["max_chars"]["description"] = "Maximum number of characters to return."

    def forward(self, relative_path: str, max_chars: int = 1200) -> dict[str, Any]:
        target = self._safe_path(relative_path)
        if not target.exists():
            raise FileNotFoundError(f"{relative_path!r} does not exist")
        if not target.is_file():
            raise IsADirectoryError(f"{relative_path!r} is not a file")
        content = target.read_text(encoding="utf-8")
        return {
            "path": str(target.relative_to(self.workspace_root)),
            "content": content[:max_chars],
            "truncated": len(content) > max_chars,
        }

    def _safe_path(self, relative_path: str) -> Path:
        target = (self.workspace_root / relative_path).resolve()
        if target != self.workspace_root and self.workspace_root not in target.parents:
            raise ValueError("Path escapes the workspace root")
        return target


class AddNumbersTool(BaseTool):
    def __init__(self):
        super().__init__(
            name="add_numbers",
            description="Add two numbers and return the sum.",
            category=ToolCategory.CALCULATION,
        )
        self.parameters.data["a"]["description"] = "First number."
        self.parameters.data["b"]["description"] = "Second number."

    def forward(self, a: float, b: float) -> float:
        return a + b


class DeepSeekReactAgent(BaseAgent):
    def __init__(self, workspace_root: Path, model_name: str = "deepseek-chat"):
        super().__init__()
        api_key = os.environ.get("DEEPSEEK_API_KEY")
        if not api_key:
            raise RuntimeError("DEEPSEEK_API_KEY is missing. Put it in .env or export it first.")

        self.llm = AsyncRequestsClient(
            base_url="https://api.deepseek.com",
            api_key=api_key,
            model_name=model_name,
            temperature=0.1,
            timeout=60,
            stream=True,
        )
        self.tool_manager = ToolManager()
        self.tool_manager.register_tool(ListFilesTool(workspace_root))
        self.tool_manager.register_tool(ReadTextFileTool(workspace_root))
        self.tool_manager.register_tool(AddNumbersTool())

    def tools_param(self) -> list[dict[str, Any]]:
        funcs = self.tool_manager.get_openai_functions()
        return [{"type": "function", "function": function} for function in funcs]

    def verify_tools(self) -> None:
        functions = self.tool_manager.get_openai_functions()
        names = [function["name"] for function in functions]
        assert names == ["list_files", "read_text_file", "add_numbers"], names

        checks = {
            "list_files": self.tool_manager.call_tool("list_files", relative_path="."),
            "read_text_file": self.tool_manager.call_tool(
                "read_text_file",
                relative_path="examples/react_agent_demo.py",
                max_chars=80,
            ),
            "add_numbers": self.tool_manager.call_tool("add_numbers", a=2, b=40),
        }
        failures = {name: result for name, result in checks.items() if not result.get("success")}
        if failures:
            raise RuntimeError(f"Tool verification failed: {json.dumps(failures, ensure_ascii=False)}")

        print("Tool verification passed:", ", ".join(names))

    async def stream(
        self,
        user_input: str,
        max_steps: int = 8,
    ) -> AsyncIterator[Union[BaseMessageChunk, ToolMessage]]:
        context = BaseContext(
            system_prompt=(
                "You are a concise ReAct agent. Use tools when they help, "
                "then answer the user in Chinese."
            )
        )
        context.add_message(UserMessage(content=user_input))

        for _ in range(max_steps):
            async for chunk in self.llm.stream_forward(context, tools=self.tools_param()):
                yield chunk

            assistant_message = context.last_message
            tool_calls = list(_message_tool_calls(assistant_message))
            if not tool_calls:
                return

            for tool_call in tool_calls:
                name = tool_call.tool_name.data
                kwargs = dict(tool_call.arguments.data)
                result = self.tool_manager.call_tool(name, **kwargs)
                tool_message = ToolMessage(
                    content=json.dumps(result, ensure_ascii=False),
                    tool_call_id=tool_call.tool_call_id.data,
                )
                context.add_message(tool_message)
                yield tool_message

        raise RuntimeError(f"Agent stopped after max_steps={max_steps}")

    async def run_stream(self, user_input: str, max_steps: int = 8) -> str:
        parts = []
        async for message in self.stream(user_input, max_steps=max_steps):
            if isinstance(message, BaseMessageChunk):
                parts.append(_message_content(message))
        return "".join(parts)

    async def run(self, user_input: str, max_steps: int = 8) -> str:
        return await self.run_stream(user_input, max_steps=max_steps)


def _message_tool_calls(message: Any) -> Iterable[Any]:
    tool_calls = getattr(message, "tool_calls", None)
    if tool_calls is None:
        return []
    return getattr(tool_calls, "data", tool_calls) or []


def _message_content(message: Any) -> str:
    content = getattr(message, "content", "")
    return getattr(content, "data", content) or ""


def _message_role(message: Any) -> str:
    role = getattr(message, "role", "")
    return getattr(role, "data", role) or ""


async def main() -> None:
    parser = argparse.ArgumentParser(description="Run a small pygent + DeepSeek ReAct demo.")
    parser.add_argument(
        "prompt",
        nargs="?",
        default=(
            "请先列出 examples 目录，再读取 examples/react_agent_demo.py 的前面内容，"
            "最后计算 2+40，并用一句话总结你做了什么。"
        ),
    )
    args = parser.parse_args()

    workspace_root = Path(__file__).resolve().parents[1]
    _load_env_file(workspace_root / ".env")

    agent = DeepSeekReactAgent(workspace_root=workspace_root)
    agent.verify_tools()
    print("Agent stream:")
    async for message in agent.stream(args.prompt):
        if isinstance(message, BaseMessageChunk):
            print(_message_content(message), end="", flush=True)
        elif _message_role(message) == "tool":
            payload = json.loads(_message_content(message))
            tool_name = payload.get("metadata", {}).get("tool", "unknown")
            success = payload.get("success")
            print(f"\n[tool:{tool_name} success={success}]\n", end="", flush=True)
    print()


if __name__ == "__main__":
    asyncio.run(main())
