# Before/After Gotcha Demo Source

This is a lightweight source transcript for the short "viral moment" demo:
an agent returns to a repo and either needs the same gotcha explained again,
or recalls it from Agent Memory Bridge before touching code.

The story is intentionally public-safe:

- no private paths
- no customer data
- no personal operating profile
- no model/vendor-specific claims
- no binary assets required

## Cast Setup

Use this as narration source for a README section, terminal recording, or short
video. The transcript is written in asciicast-friendly terminal blocks, but it is
not itself a generated `.cast` file.

Commands shown as `amb store` and `amb recall` are narrative shorthand for
Agent Memory Bridge MCP tool calls. They are not a shipped shell CLI contract.

Placeholder project:

```text
repo: widget-api
gotcha: after editing api/schema.yaml, run npm run generate:client before tests
symptom: tests fail with stale client types even though the schema change is correct
fix: run npm run generate:client, then npm test
memory namespace: project:widget-api
```

## Scene 1: Without Agent Memory Bridge

```text
$ agent "Update the user status field in api/schema.yaml and run tests."

agent> I'll inspect the schema and make the change.
agent> Tests are failing in client type checks. The schema looks right, so I am
       checking the test setup and TypeScript config.

user> This repo has a gotcha: every schema edit needs `npm run generate:client`
      before tests. We hit this last time too.

agent> Thanks. Running the client generator now.

$ npm run generate:client
generated client from api/schema.yaml

$ npm test
all tests passed
```

Narration beat:

```text
Same repo. Same gotcha. Same explanation again.
The agent can recover, but the human has to be the memory layer.
```

## Scene 2: Seed The Prior Session Memory

This scene represents what the previous successful session wrote after the user
taught the repo-specific gotcha.

```text
$ amb store \
  --namespace project:widget-api \
  --kind memory \
  --title "[[Gotcha]] regenerate API client after schema edits" \
  --tag kind:gotcha \
  --tag domain:codegen \
  --tag topic:schema \
  --content "trigger: editing api/schema.yaml
symptom: client type tests fail with stale generated types
fix: run npm run generate:client before npm test"

stored memory: [[Gotcha]] regenerate API client after schema edits
```

Narration beat:

```text
The memory is compact, repo-scoped, and reusable.
It captures the trigger, symptom, and fix instead of a whole chat transcript.
```

## Scene 3: With Agent Memory Bridge

```text
$ agent "Update the user status field in api/schema.yaml and run tests."

agent> Before editing, I am checking project memory for repo-specific gotchas.

$ amb recall \
  --namespace project:widget-api \
  --query "schema yaml tests client types gotcha"

hit: [[Gotcha]] regenerate API client after schema edits
trigger: editing api/schema.yaml
symptom: client type tests fail with stale generated types
fix: run npm run generate:client before npm test

agent> Found a repo gotcha from a previous session: schema edits require client
       regeneration before tests. I will include that in the workflow.

$ edit api/schema.yaml
$ npm run generate:client
generated client from api/schema.yaml

$ npm test
all tests passed
```

Narration beat:

```text
The second session starts smarter.
The user does not re-teach the repo.
The agent remembers the sharp edge before it becomes a failure.
```

## Optional 20-Second Cut

```text
WITHOUT AMB
user> We hit this last time too: run the generator after schema edits.

WITH AMB
agent> I found the previous gotcha: run the generator after schema edits.
agent> I will do that before tests.
```

## Use Notes

This file is the source of the demo story. There is nothing to regenerate unless
you intentionally turn it into a rendered asset.

To render it manually:

1. Record a terminal session that follows the blocks above.
2. Save any generated asciicast as `examples/demo/before-after-gotcha.cast`.
3. If rendering a GIF, use:

```bash
agg ./examples/demo/before-after-gotcha.cast ./examples/demo/before-after-gotcha.gif
```

Do not commit generated binary assets unless the release checklist explicitly
asks for them.
