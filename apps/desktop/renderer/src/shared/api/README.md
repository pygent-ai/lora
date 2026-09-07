# API Client

The local API client streams execution events and resumes an interrupted stream using its execution ID and sequence cursor. EOF without a terminal event triggers reconnect; the retry budget starts at disconnection, not at task submission. Connection state is reported separately from task completion.
