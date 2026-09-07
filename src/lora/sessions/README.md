# Sessions

Owns session creation, loading, mutation, and on-disk persistence through `SessionManager`.

It depends on core helpers and schema contracts, and stays independent of runtime orchestration.

Project sessions live below `~/.lora/projects/<canonical-path-hash>/sessions`;
projectless sessions use `~/.lora/conversations/sessions`. Runtime databases and
project skills share the same data root. Creating a session does not create a
`.lora` directory inside the workspace.

Starting a case records its history boundary and publishes the running case ID
in session metadata. Native assistant messages preserve usage counters and
reasoning display metadata across checkpoints and application restarts.
