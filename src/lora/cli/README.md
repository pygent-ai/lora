# CLI

Thin command-line adapter over Lora application services.

- `main.py`: parser and command dispatch.
- `credentials.py`: credential subcommands.

Business logic belongs in the corresponding feature package, not in command handlers.
