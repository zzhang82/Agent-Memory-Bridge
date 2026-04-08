# Demo

This folder holds the reusable terminal demo source for Agent Memory Bridge.

The current terminal demo shows:

- one durable `memory`
- one coordination `signal`
- `claim_signal`
- `extend_signal_lease`
- `ack_signal`
- a small benchmark snapshot

Files:

- `scripts/demo_terminal.py` for the terminal flow itself
- `scripts/build_demo_cast.py` to regenerate the asciicast
- `terminal-demo.cast` as the canonical recorded source
- `terminal-demo.tape` as an optional VHS source

To regenerate the cast:

```powershell
.\.venv\Scripts\python.exe .\scripts\build_demo_cast.py
```

To render the cast to GIF after installing `agg`:

```powershell
agg .\examples\demo\terminal-demo.cast .\examples\demo\terminal-demo.gif
```
