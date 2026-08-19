# Config

Loads runtime/project behavior and agent/model settings from user-level
`~/.lora/config.yaml`, and credentials from user-level and workspace fallback
sources.

This repository intentionally uses one user config source; when user config does
not define a setting it is loaded from built-in defaults.

`loader.py` may depend on schema and credentials, but not on runtime, evaluation, CLI, or API adapters.
