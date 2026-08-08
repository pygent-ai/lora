from __future__ import annotations

from lora.schema import AgentSession


class LoraExecutionContext:
    """Lora session state attached to one native Pygent execution."""

    def __init__(self, session: AgentSession):
        self.session = session
        self.session_id = session.session_id
        self.system_prompt = session.system_prompt
        self.history = session.history

