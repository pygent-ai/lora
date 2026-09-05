# Projects Feature

`ProjectPicker` is the shared project switcher opened from the folder button in the
sidebar title bar. It searches known projects by name
or path, shows full paths to distinguish equal names, and accepts pasted absolute
paths (including quoted Windows paths). Recent projects come from the backend.

Choosing a recent project switches immediately. Open path / Enter opens a pasted
path; selecting the current project closes without reloading. Electron also offers
Browse folders, starting at the entered path or current project. Browser previews
use the same picker with manual path entry. Canceling the native dialog does nothing.

Switching submits only the workspace and clears the agent override so the target
project can resolve its default. Model routes and context limits are not resubmitted.
Running chats temporarily prevent switching, and pending requests disable duplicate
actions. Invalid folders are rejected by the settings endpoint before runtime reload;
errors stay in the picker with the entered path intact. The native HTML dialog
provides modal focus containment and Escape handling.

The title bar uses a plain plus for a chat in the active project and a familiar folder
for opening a project. Every project group also has a trailing plus button that creates
a chat in that specific project and selects it; project rows omit session counts. The
Chat group has the same plus affordance and creates a conversation-scoped chat without
changing the active project.
