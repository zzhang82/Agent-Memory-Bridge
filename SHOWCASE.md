# Showcase

Named namespaces beat unsigned releases. This page is the public place to put
them. Agent Memory Bridge (AMB) by zzhang82 does not invent testimonials.

## What a useful quote looks like

> I used this on \<project\>. Session two already knew Y, including the reason.

Until someone who starred or used the repo says that in writing, this page stays
empty of quotes. Empty is more trustworthy than filler.

## Ask (star-gazers and early users)

If AMB already sits on one of your repos and you are willing to be named:

1. What project namespace did you bind?
2. What decision did the second session remember?
3. May we quote one sentence here and in GitHub Discussions?

Open a Discussion or an issue. Do not send transcripts. A single named decision
is enough.

## Seeded Discussions

When Discussions are used, start with three topics:

1. **Show your project namespace** — one binding, one decision, one second-session win.
2. **Client setup help** — Claude Code first; other clients under Integrations.
3. **Why not AGENTS.md?** — AMB complements `AGENTS.md` / `CLAUDE.md`. It does not scrape transcripts.

## Reproduce a first-session → second-session win

Use a **disposable demo repository or a real decision that is actually true for
your project**. AMB should not seed canned claims into governed project memory.

```bash
pip install agent-memory-bridge==0.32.2
python -m venv .amb-venv
# then, from the venv Python:
# <venv-python> -m agent_mem_bridge setup --client claude-code --apply
# <venv-python> -m agent_mem_bridge project init .
```

When Explore says WHY is empty, ask the connected agent to help you phrase one
real project decision and its reason, confirm the wording, then store it through
the existing public memory tools. Open a fresh session and ask about that same
topic. The first win is that the decision **and its reason** come back without
re-teaching the project.

A later public demo repo (`amb-demo`) can freeze a disposable example decision. It is not
this repository.
