# CLI

Thin command-line adapter over Lora application services.

- `main.py`: root parser, command dispatch, JSON rendering, and CLI errors.
- `sessions.py`: session CRUD, one-shot turns, interactive chat, and collaboration commands.
- `collaboration_worker.py`: detached process entry point for one durable queued operation.
- `credentials.py`: credential subcommands.

Business logic belongs in the corresponding feature package, not in command handlers.
