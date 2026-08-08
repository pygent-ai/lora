from __future__ import annotations

import json

import pytest
from pygent.core import ExecutionEvent as PygentExecutionEvent
from pydantic import ValidationError

from lora_api.models.requests import ChatTurnRequest, UpdateSettingsRequest
from lora_api.services.chat_runner import _execution_event, _sse


def test_execution_event_preserves_native_journal_contract() -> None:
    event = _execution_event(
        {
            "schema_version": "1",
            "event_id": "event-1",
            "execution_id": "exec-1",
            "trace_id": "trace-1",
            "span_id": "span-1",
            "parent_span_id": None,
            "sequence": 7,
            "timestamp_unix_ns": 123,
            "kind": "model.text.delta",
            "module_path": "lora.react.model",
            "data": {"text": "hello"},
        }
    )
    assert event.model_dump() == {
        "schema_version": "1",
        "event_id": "event-1",
        "execution_id": "exec-1",
        "trace_id": "trace-1",
        "span_id": "span-1",
        "parent_span_id": None,
        "sequence": 7,
        "timestamp_unix_ns": 123,
        "kind": "model.text.delta",
        "module_path": "lora.react.model",
        "data": {"text": "hello"},
    }
    payload = json.loads(_sse(event).split("data: ", 1)[1])
    assert payload == event.model_dump()


def test_execution_event_accepts_pygent_event_without_translation() -> None:
    raw = PygentExecutionEvent(
        schema_version="1",
        event_id="event-1",
        execution_id="exec-1",
        trace_id="trace-1",
        span_id="span-1",
        parent_span_id=None,
        sequence=1,
        timestamp_unix_ns=123,
        module_path="lora.react.model",
        kind="model.text.delta",
        data={"text": "hello"},
    )
    assert _execution_event(raw).model_dump() == {
        "schema_version": raw.schema_version,
        "event_id": raw.event_id,
        "execution_id": raw.execution_id,
        "trace_id": raw.trace_id,
        "span_id": raw.span_id,
        "parent_span_id": raw.parent_span_id,
        "sequence": raw.sequence,
        "timestamp_unix_ns": raw.timestamp_unix_ns,
        "module_path": raw.module_path,
        "kind": raw.kind,
        "data": {"text": "hello"},
    }


def test_chat_resume_uses_execution_identity_and_sequence() -> None:
    request = ChatTurnRequest(execution_id="exec-1", after_sequence=7)
    assert request.message is None
    assert request.execution_id == "exec-1"
    assert request.after_sequence == 7


@pytest.mark.parametrize(
    "payload",
    [
        {"message": "hello", "unknown_a": "value"},
        {"message": "hello", "unknown_b": 3},
    ],
)
def test_unknown_chat_fields_are_rejected(payload: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        ChatTurnRequest(**payload)


def test_unknown_settings_field_is_rejected() -> None:
    with pytest.raises(ValidationError):
        UpdateSettingsRequest(unknown="value")
