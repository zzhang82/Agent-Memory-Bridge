# Run Consolidation (Shadow Only)

`agent-memory-bridge consolidate-runs` reads completed episode-ledger runs and
turns explicitly shaped decision evidence into reviewable lesson candidates.
It is intentionally separate from the historical memory consolidation engine.

```text
agent-memory-bridge consolidate-runs --shadow --workspace-key project:example
agent-memory-bridge consolidate-runs --shadow --workspace-key project:example --format json
agent-memory-bridge consolidate-runs --shadow --workspace-key project:example --stage
```

`--shadow` is required. Without `--stage`, the command opens the existing
database read-only and does not write run authority, derived projections,
state cursors, logs, ordinary memories, ranking, policies, prompts, or files.
The command is a shadow review surface; it does not create utility credit or
change episode authority.
A missing or unmigrated database fails cleanly instead of being initialized.
`--stage` is the sole opt-in mutation:
eligible candidates are sent to the existing hidden learning-candidate lane
with `candidate_status=needs_review`.  They remain excluded from normal recall
and cannot promote themselves.

## Evidence contract

Only an event with `event_type="decision"` and this exact payload schema is
considered:

```json
{
  "schema": "amb.run-consolidation-evidence.v1",
  "claim": "Run the focused migration proof before release.",
  "evidence_refs": ["test:migration-proof"],
  "authority_class": "procedure",
  "domain_tags": ["domain:release"],
  "goal": "Validate the migration before release.",
  "when_to_use": "Before a schema release.",
  "steps": ["Run the deterministic migration proof."],
  "failure_mode": "The proof is skipped.",
  "rollback_path": "Do not publish; investigate the failing proof."
}
```

The payload has a closed field set. Unknown fields and transcript, raw-CoT,
reasoning, message, or path-shaped payload fields are excluded with a stable
reason code. `claim` and optional narrative fields are bounded and rejected
when path-like or secret-like. `evidence_refs` are 1–16 unique opaque
references; paths, drive prefixes, slashes, backslashes, control characters,
and secret-shaped markers such as `sk-*` are rejected. Domain tags must be
sorted, unique `domain:*` values.
Callers cannot provide a score, ranking, confidence, or stance.

For `authority_class="procedure"`, AMB uses the existing procedure-governance
parser and vocabulary. `goal`, `when_to_use`, and `steps` are the minimum
fields. The generated procedure is always a draft candidate; the consolidator
never declares it validated.

## Eligibility and interpretation

The current outcome head drives classification. A nonempty schema-bounded JSON
outcome-evidence array supplies opaque evidence references, but it does not
authenticate the declared evaluator. Its values are never copied into the
report or staged candidate: each canonical JSON element becomes an opaque
`outcome-evidence-sha256:<digest>` reference. A v1 `verified_success` is
readable but classified `legacy_declared`; it is not strong verification and
cannot support a consolidation candidate. Evidence-backed `partial_success`
is also neutral for eligibility. Strong support requires a current governed-v2
verification receipt. Evidence-backed
failures, regressions, and user corrections are contradictions. An
evidence-backed current regression is also an inbound
contradiction for every candidate supported by the run it targets, even when
the regression run has no matching decision event. Superseded regression
outcomes do not block. Unverified, abandoned, active, empty-evidence negative
outcomes, and watcher `rollout_idle` closeouts are neutral or excluded. Any
contradiction makes its candidate ineligible and unavailable for staging until
the conflicting evidence is resolved.

Candidates group only an exact normalized claim, boundary, authority class,
and ordered domain tags. There is no model clustering or caller-provided hash.
At most one episode from each run contributes to a group. The current
thread/session/evidence comparison is declared independence only because those
labels are not authenticated. Blank thread/session values deliberately fall
back to the run ID. Declared independence alone cannot make a candidate eligible
because those labels are not authenticated.

An eligible candidate will require either two independent episodes carrying
strong governed-v2 support or an evidence-backed deterministic-verifier
`verified_success` in the same governed-v2 outcome chain superseded by the
current evidence-backed human `verified_success`. No v1 `verified_success`
alone qualifies. Contradictions never qualify a candidate. Confidence is a
label, not a score: `reviewed`, `corroborated`, `provisional`, or `contested`.

The JSON report is deterministic and has no wall-clock generated timestamp.
Keyset pagination scans the full workspace in one read snapshot; `limit` is the
internal page size, not a 500-run evidence ceiling. A stable
`candidate_subject_id` names the claim subject while `evidence_revision_id`
changes with the evidence set. Procedure-structure conflicts and explicit
structured opposition mark candidates contested. Re-running the same complete
evidence revision reports a duplicate; a changed revision creates a separate
hidden review candidate for the same subject.
