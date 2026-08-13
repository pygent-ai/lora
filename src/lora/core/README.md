# Core

Stable, dependency-light helpers shared by every feature domain.

- `io.py`: JSON/JSONL, path validation, timestamps, and plain-data conversion.
- `redaction.py`: secret redaction.

Core must not import runtime, evaluation, repair, CLI, API, or UI code.
