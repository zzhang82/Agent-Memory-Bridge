# Demo

This folder holds the reusable terminal demo source for Agent Memory Bridge.

The current `v0.5` demo shows:

- one durable `memory`
- one coordination `signal`
- `claim_signal`
- `extend_signal_lease`
- `ack_signal`
- a small benchmark snapshot

Files:

- `scripts/demo_v0_5.py` for the terminal flow itself
- `scripts/build_demo_cast.py` to regenerate the asciicast
- `v0.5-terminal-demo.cast` as the canonical recorded source
- `v0.5-terminal-demo.tape` as an optional VHS source

To regenerate the cast:

```powershell
.\.venv\Scripts\python.exe .\scripts\build_demo_cast.py
```

To render the cast to GIF after installing `agg`:

```powershell
agg .\examples\demo\v0.5-terminal-demo.cast .\examples\demo\v0.5-terminal-demo.gif
```
