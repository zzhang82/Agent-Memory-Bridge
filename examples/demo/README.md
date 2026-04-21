# Demo

This folder holds the reusable terminal demo source for Agent Memory Bridge.

The current terminal demo shows:

- a small durable memory bundle shaped like `belief -> concept-note -> procedure`
- one coordination `signal`
- `claim_signal`
- `extend_signal_lease`
- `ack_signal`
- a later `recall_first(...)` moment where the bridge surfaces useful task memory
- a small benchmark snapshot

The checked-in demo is meant to show both coordination state and a later
"the agent remembered something useful" retrieval moment, while still matching
the current benchmark snapshot.
Historical `v0.5` demo assets are kept separately and are not the primary public demo.

Files:

- `scripts/demo_terminal.py` for the terminal flow itself
- `scripts/build_demo_cast.py` to regenerate the asciicast
- `terminal-demo.cast` as the canonical recorded source
- `terminal-demo.tape` as an optional VHS source

To regenerate the cast:

```bash
python ./scripts/build_demo_cast.py
```

Windows PowerShell:

```powershell
.\.venv\Scripts\python.exe .\scripts\build_demo_cast.py
```

The checked-in cast uses a scrubbed placeholder prompt path so the public demo
does not expose a maintainer workstation path.

To render the cast to GIF after installing `agg`:

```bash
agg ./examples/demo/terminal-demo.cast ./examples/demo/terminal-demo.gif
```

Windows PowerShell:

```powershell
agg .\examples\demo\terminal-demo.cast .\examples\demo\terminal-demo.gif
```
