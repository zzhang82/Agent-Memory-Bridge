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

There is also a lightweight before/after story source for the "viral moment":
without Agent Memory Bridge, the user has to re-teach a repo gotcha; with Agent
Memory Bridge, the next session recalls the gotcha before touching code.

The checked-in demo is meant to show both coordination state and a later
"the agent remembered something useful" retrieval moment, while still matching
the current benchmark snapshot.
Historical `v0.5` demo assets are kept separately and are not the primary public demo.

Files:

- `before-after-gotcha.cast.md` as a text-only source transcript for the before/after gotcha story
- `scripts/demo_terminal.py` for the terminal flow itself
- `scripts/build_demo_cast.py` to regenerate the asciicast
- `terminal-demo.cast` as the canonical recorded source
- `terminal-demo.tape` as an optional VHS source

To use the before/after gotcha story:

```bash
python -c "from pathlib import Path; print(Path('examples/demo/before-after-gotcha.cast.md').read_text())"
```

It is source material only. If you want to turn it into a shareable recording,
save the generated asciicast as `examples/demo/before-after-gotcha.cast`; if you
render a GIF from that cast, use:

```bash
agg ./examples/demo/before-after-gotcha.cast ./examples/demo/before-after-gotcha.gif
```

The checked-in text source is enough for readers; generated binaries are
optional release assets.

To regenerate the cast:

```bash
python ./scripts/build_demo_cast.py
```

The checked-in cast uses a scrubbed placeholder prompt path so the public demo
does not expose a maintainer workstation path.

To render the cast to GIF after installing `agg`:

```bash
agg ./examples/demo/terminal-demo.cast ./examples/demo/terminal-demo.gif
```
