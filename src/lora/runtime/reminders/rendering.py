from __future__ import annotations

from datetime import datetime

from .models import ReminderSection


def render_reminder(
    sections: list[ReminderSection],
    *,
    rendered_at: datetime | None = None,
) -> str | None:
    active = sorted(
        (section for section in sections if section.lines),
        key=lambda item: (item.order, item.source),
    )
    if not active:
        return None
    timestamp = rendered_at or datetime.now().astimezone()
    lines = ["<system-reminder>"]
    if any(section.include_time for section in active):
        lines.extend(
            [
                "<time>",
                f"  Current system time: {timestamp.strftime('%Y-%m-%d %H:%M:%S %Z')}",
                "</time>",
                "",
            ]
        )
    for index, section in enumerate(active):
        if index:
            lines.append("")
        lines.extend(section.lines)
    lines.append("</system-reminder>")
    return "\n".join(lines)
