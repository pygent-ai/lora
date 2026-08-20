# Electron Main

Placeholder for the Electron main process.

This layer owns window lifecycle, local FastAPI process startup, shutdown, logs, and native desktop integration.
Electron user data and logs are stored under `~/.lora/desktop`; model configuration,
credentials, and GUI project state remain under their dedicated `~/.lora` paths.
