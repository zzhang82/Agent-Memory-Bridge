# Demo

The public story is the first-win loop: one stdio process stores an explicit decision, and a second process on the same AMB home recalls the claim and the reason.

That recording is [first-win-demo.cast](first-win-demo.cast). It is a local server proof, not a filmed coding-agent session. Regenerate it with:

```bash
python ./scripts/build_first_win_cast.py
```

The generator runs [scripts/demo_first_win.py](../../scripts/demo_first_win.py), checks that the recalled record matches the stored decision, and scrubs workstation paths before writing the cast. The cast header time and line delays are fixed playback, not a measurement of that run. Rendering a GIF needs `agg`. This folder does not include a first-win GIF. When `agg` is available:

```bash
agg ./examples/demo/first-win-demo.cast ./examples/demo/first-win-demo.gif
```

Do not check in a GIF that was not rendered from that cast.

## Coordination demo

[terminal-demo.cast](terminal-demo.cast) and [terminal-demo.gif](terminal-demo.gif) are the older coordination recording. They show:

- a durable memory bundle shaped like `belief -> concept-note -> procedure`
- one coordination `signal`
- `claim_signal`
- `extend_signal_lease`
- `ack_signal`
- a later `recall_first(...)` moment
- a small benchmark snapshot

That recording stays because the release check requires `terminal-demo.cast` and `terminal-demo.gif`. It is not the public first-win story. Historical `v0.5` assets are kept separately and are not the primary public demo.

Regenerate the coordination cast with:

```bash
python ./scripts/build_demo_cast.py
```

The checked-in cast uses a scrubbed placeholder prompt path so the public demo does not expose a maintainer workstation path. Optional VHS source: `terminal-demo.tape`.

To render that cast after installing `agg`:

```bash
agg ./examples/demo/terminal-demo.cast ./examples/demo/terminal-demo.gif
```

## Other sources

The before/after gotcha note, [before-after-gotcha.cast.md](before-after-gotcha.cast.md), is narration only. Commands written as `amb store` and `amb recall` are not a shipped shell CLI. Do not turn that transcript into the public recording.

```bash
python -c "from pathlib import Path; print(Path('examples/demo/before-after-gotcha.cast.md').read_text())"
```

The v0.30 project-knowledge proof is separate at [v030-project-knowledge/](v030-project-knowledge/). It does not replace the first-win cast or the coordination recording.
