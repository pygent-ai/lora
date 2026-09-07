# Package Scripts

Windows desktop packaging is split into two steps:

1. `build-python-api.ps1` uses PyInstaller through `uv` to create `build/package/lora-api/lora-api.exe` and `build/package/lora-api/lora.exe`.
2. `build-desktop.ps1` builds the React renderer and runs electron-builder. The PyInstaller output is copied into Electron resources as `backend/lora-api`.

The resulting Electron app starts the bundled `lora-api.exe` at launch, and the Windows installer adds the bundled CLI directory to the current user's PATH. Target machines do not need Python, `uv`, or project Python dependencies installed. After installation, open a new terminal to run commands such as:

```powershell
lora chat --new -m "hello"
```

Useful commands from the repository root:

```powershell
npm run desktop:portable
npm --prefix apps/desktop run package:dir
npm --prefix apps/desktop run package:win
```

`npm run desktop:portable` creates a standalone `Lora-Desktop-<version>-portable.exe` in `apps/desktop/release`. It can be copied to another Windows x64 machine and opened directly without an installer. The portable build does not modify `PATH`; use the NSIS installer when the `lora` terminal command is also required.

`build-desktop.ps1` sets default Electron download mirrors for China-friendly packaging. Override `ELECTRON_MIRROR` or `ELECTRON_BUILDER_BINARIES_MIRROR` before running the script if you use a different mirror.
