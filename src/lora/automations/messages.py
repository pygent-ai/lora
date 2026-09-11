from __future__ import annotations

from html import escape

from pygent import UserMessage, freeze_json_object

from .models import Automation, AutomationRun

AUTOMATION_MESSAGE_KIND = "lora.automation.trigger"


def automation_trigger_message(
    automation: Automation, run: AutomationRun
) -> UserMessage:
    content = "\n".join(
        (
            (
                '<automation-trigger schema-version="1" '
                f'automation-id="{escape(automation.automation_id, quote=True)}" '
                f'run-id="{escape(run.run_id, quote=True)}">'
            ),
            f"  <task-name>{escape(automation.name, quote=False)}</task-name>",
            f"  <scheduled-for>{escape(run.scheduled_for, quote=False)}</scheduled-for>",
            f"  <timezone>{escape(automation.timezone, quote=False)}</timezone>",
            f"  <mode>{escape(automation.destination, quote=False)}</mode>",
            "  <unattended>true</unattended>",
            "  <instructions>",
            f"{escape(automation.prompt, quote=False)}",
            "  </instructions>",
            "</automation-trigger>",
        )
    )
    return UserMessage(
        content=content,
        kind=AUTOMATION_MESSAGE_KIND,
        data=freeze_json_object(
            {
                "origin": "automation",
                "automation_id": automation.automation_id,
                "automation_run_id": run.run_id,
                "raw_content": automation.prompt,
            }
        ),
    )


__all__ = ["AUTOMATION_MESSAGE_KIND", "automation_trigger_message"]
