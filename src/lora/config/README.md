# Config

Loads project behavior from `<workspace>/lora.yaml`, user-level agent/model settings from
`~/.lora/config.yaml`, and credentials from user-level or compatibility fallback sources.

When the user config exists, its `agent` and `agents` sections replace the corresponding
project sections. Project-level agent/model settings remain a compatibility fallback only.

`loader.py` may depend on schema and credentials, but not on runtime, evaluation, CLI, or API adapters.
