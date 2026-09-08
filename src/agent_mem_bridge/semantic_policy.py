"""Canonical semantic policy for deciding when an agent should use AMB memory.

The policy is deliberately separate from any host's instruction or configuration
format.  The structured values in this module are the source of truth; Markdown
and JSON output are derived views for manual, reviewable host integration.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from typing import Any, Literal

POLICY_SCHEMA = "amb.semantic-memory-policy.v1"
POLICY_VERSION = "canonical-policy-v1"
POLICY_MARKER = "AMB:semantic-memory-policy"

PolicyHost = Literal["generic", "codex", "opencode"]
PolicyFormat = Literal["markdown", "json"]


@dataclass(frozen=True)
class PolicyRule:
    """One semantic invocation rule shared by every host renderer."""

    id: str
    instruction: str

    def as_dict(self) -> dict[str, str]:
        return asdict(self)


@dataclass(frozen=True)
class HostAdapter:
    """Host placement metadata; it does not change policy semantics."""

    host: PolicyHost
    instruction_surface: str
    placement: str
    loading_claim: str

    def as_dict(self) -> dict[str, str]:
        return asdict(self)


@dataclass(frozen=True)
class RenderedPolicy:
    host: PolicyHost
    format: PolicyFormat
    content: str
    policy_version: str
    policy_sha256: str
    adapter: HostAdapter

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema": POLICY_SCHEMA,
            "version": self.policy_version,
            "policy_sha256": self.policy_sha256,
            "host": self.host,
            "adapter": self.adapter.as_dict(),
            "policy": canonical_policy(),
        }


MUST_RECALL_RULES = (
    PolicyRule(
        "material-prior-context",
        "The request depends on an earlier project decision, constraint, unresolved item, failure, or rationale that could change the current choice.",
    ),
    PolicyRule(
        "fresh-session-or-handoff",
        "A fresh session, compaction, handoff, or cross-client continuation resumes project work where prior context could materially affect the next action.",
    ),
    PolicyRule(
        "constrained-architecture-choice",
        "An architecture, schema, release, security, or governance choice may be constrained by project-specific history.",
    ),
    PolicyRule(
        "known-project-gotcha",
        "The task resembles a known project gotcha, failure, or recovery procedure.",
    ),
    PolicyRule(
        "indirect-history-reference",
        "The wording refers indirectly to how this repository is kept safe or to an earlier project choice, even if it does not say memory, recall, or remember.",
    ),
    PolicyRule(
        "live-history-conflict",
        "Current evidence appears to conflict with a prior project decision and needs reconciliation.",
    ),
)

MAY_RECALL_RULES = (
    PolicyRule(
        "large-design-choice",
        "A large refactor or design choice has several valid alternatives and project preference may matter.",
    ),
    PolicyRule(
        "history-may-resolve-ambiguity",
        "Project history is a plausible way to resolve an ambiguity, but no material dependency is yet established.",
    ),
)

DO_NOT_RECALL_RULES = (
    PolicyRule(
        "not-for-tool-use",
        "Do not recall merely because any tool or skill is being used.",
    ),
    PolicyRule(
        "not-for-every-reasoning-step",
        "Do not recall merely because another reasoning step or file edit started.",
    ),
    PolicyRule(
        "not-for-deterministic-edit",
        "Do not recall for an isolated typo, formatting change, or deterministic local fix.",
    ),
    PolicyRule(
        "not-when-live-evidence-is-sufficient",
        "Do not recall when current deterministic evidence already answers the question.",
    ),
    PolicyRule(
        "not-repeat-sufficient-recall",
        "Do not repeat a sufficient recent recall without a new material need.",
    ),
)

DECISION_LABELS = (
    "must_recall",
    "may_recall",
    "do_not_recall",
    "recall_and_reconcile",
    "must_recall_then_no_hit",
    "recall_or_report_unavailable",
    "do_not_repeat",
)

FAILURE_CLASSES = (
    "policy_miss",
    "host_miss",
    "adapter_miss",
    "tool_unavailable",
    "retrieval_miss",
    "memory_misuse",
    "evidence_unavailable",
    "unsupported_memory_claim",
)

_HOST_ADAPTERS: dict[PolicyHost, HostAdapter] = {
    "generic": HostAdapter(
        host="generic",
        instruction_surface="host-defined project instruction surface",
        placement="Manually place the reviewed block wherever this host loads project instructions; no universal path is assumed.",
        loading_claim="The adapter does not verify that a generic host loaded or followed the block.",
    ),
    "codex": HostAdapter(
        host="codex",
        instruction_surface="project AGENTS.md or AGENTS.override.md",
        placement="Review and merge the marked block into the project-root AGENTS.md, unless a deliberate AGENTS.override.md takes precedence.",
        loading_claim="Codex instruction loading and override precedence are host/runtime facts; this renderer does not certify that a session loaded the file.",
    ),
    "opencode": HostAdapter(
        host="opencode",
        instruction_surface="project AGENTS.md or an explicitly configured project instruction file",
        placement="Review and merge the marked block into the project instruction file used by OpenCode.",
        loading_claim="OpenCode instruction loading is a host/runtime fact; this renderer does not certify that a session loaded the file.",
    ),
}


def supported_policy_hosts() -> tuple[PolicyHost, ...]:
    """Return the host adapters with first-class v0.33.1 renderers."""

    return tuple(_HOST_ADAPTERS)


def host_adapter(host: str) -> HostAdapter:
    """Return bounded placement metadata for one supported host."""

    normalized = host.strip().lower()
    if normalized not in _HOST_ADAPTERS:
        supported = ", ".join(supported_policy_hosts())
        raise ValueError(f"Unsupported policy host '{host}'. Supported hosts: {supported}.")
    return _HOST_ADAPTERS[normalized]


def canonical_policy() -> dict[str, Any]:
    """Return the JSON-safe canonical semantic policy."""

    return {
        "schema": POLICY_SCHEMA,
        "version": POLICY_VERSION,
        "purpose": "Decide when a coding agent should consult configured AMB memory without making recall mandatory for every action.",
        "decision_rules": {
            "must_recall": [rule.as_dict() for rule in MUST_RECALL_RULES],
            "may_recall": [rule.as_dict() for rule in MAY_RECALL_RULES],
            "do_not_recall": [rule.as_dict() for rule in DO_NOT_RECALL_RULES],
        },
        "workflow": [
            "Classify semantic project-history need rather than matching literal memory keywords.",
            "For a must_recall or may_recall case, make one bounded recall in the relevant project namespace before the material decision or edit.",
            "Treat a successful empty recall as no relevant memory; treat an error or unavailable host as unavailable, not as an empty result.",
            "Check lifecycle, authority, relevance, and current repository evidence. Current live evidence outranks stale or conflicting history.",
            "Use or reject recalled guidance explicitly; never claim a memory was consulted without observable invocation evidence.",
            "Use existing run events when recording evidence: receipt-bound memory_recalled, source-linked memory_applied or memory_rejected, and bounded observation events for invocation status.",
        ],
        "failure_classes": list(FAILURE_CLASSES),
        "boundaries": [
            "Invocation policy is not a router, classifier, scheduler, watcher, or hosted runtime.",
            "The policy does not inspect hidden reasoning or persist transcripts.",
            "Durable memory is written only through an explicit reviewed store or governed mutation request; the policy never performs automatic writeback.",
            "Policy output and evaluation evidence are derived views, not a new durable authority.",
        ],
    }


def canonical_policy_json() -> str:
    """Return the stable serialized policy used to calculate its digest."""

    return _canonical_json(canonical_policy())


def policy_sha256() -> str:
    """Return the SHA-256 identity of the canonical policy source."""

    return hashlib.sha256(canonical_policy_json().encode("utf-8")).hexdigest()


_DECISION_MARKERS = (
    "we decided",
    "decided previously",
    "prior decision",
    "previous decision",
    "recorded constraint",
    "project rule",
    "project constraint",
)
_FRESH_SESSION_MARKERS = (
    "fresh session",
    "new session",
    "handoff",
    "compaction",
    "pick up",
    "other coding client",
    "cross-client",
    "from the other",
)
_ARCHITECTURE_MARKERS = (
    "architecture",
    "schema",
    "new table",
    "run ledger",
    "durable authority",
    "migration",
    "release check",
    "security",
    "governance",
)
_GOTCHA_MARKERS = (
    "same failure",
    "same gotcha",
    "hit this same",
    "cross-client configuration failure",
    "known failure",
)
_INDIRECT_HISTORY_MARKERS = (
    "way this repository has been kept safe",
    "honor the way",
    "project-specific exception",
)
_CONFLICT_MARKERS = (
    "old setup",
    "superseded",
    "stale",
    "remembered release",
    "current server shows",
    "which should guide",
    "conflict",
    "setup --force",
)
_UNAVAILABLE_MARKERS = ("unavailable", "memory service may be unavailable", "cannot reach")
_NO_HIT_MARKERS = ("whether a project-specific exception exists", "if any project exception")
_TRIVIAL_MARKERS = ("misspelled", "typo", "formatting", "import fix", "run formatting", "deterministic")
_REPEAT_MARKERS = ("just recalled", "without retrieving it again", "do not retrieve it again")
_LITERAL_MEMORY_MARKERS = ("memory", "recall", "remember", "previous")
_MATERIAL_MARKERS = (
    *_DECISION_MARKERS,
    *_FRESH_SESSION_MARKERS,
    *_ARCHITECTURE_MARKERS,
    *_GOTCHA_MARKERS,
    *_INDIRECT_HISTORY_MARKERS,
    *_CONFLICT_MARKERS,
    "project history",
    "prior project",
    "continue using",
    "constraint",
    "gotcha",
)


def classify_task(
    prompt: str,
    *,
    prior_recall_sufficient: bool = False,
    amb_available: bool = True,
    arm: str = "C",
) -> str:
    """Classify a task against the frozen semantic-invocation decisions.

    Arm A approximates current keyword-triggered host behavior. Arm B is the
    one-sentence competent baseline. Arm C is the canonical policy. This helper
    is evaluation-only and does not call AMB or mutate memory.
    """

    text = " ".join(prompt.lower().split())
    if arm not in {"A", "B", "C"}:
        raise ValueError("arm must be A, B, or C")
    if arm == "A":
        if prior_recall_sufficient:
            return "do_not_repeat"
        return "must_recall" if any(marker in text for marker in _LITERAL_MEMORY_MARKERS) else "do_not_recall"
    if prior_recall_sufficient or any(marker in text for marker in _REPEAT_MARKERS):
        return "do_not_repeat"
    trivial = any(marker in text for marker in _TRIVIAL_MARKERS) and not any(
        marker in text
        for marker in _DECISION_MARKERS + _ARCHITECTURE_MARKERS + _GOTCHA_MARKERS + _INDIRECT_HISTORY_MARKERS
    )
    if trivial:
        return "do_not_recall"
    if arm == "B":
        if any(marker in text for marker in _MATERIAL_MARKERS):
            return "recall_or_report_unavailable" if not amb_available else "must_recall"
        return "do_not_recall"
    if (not amb_available or any(marker in text for marker in _UNAVAILABLE_MARKERS)) and any(
        marker in text
        for marker in _DECISION_MARKERS
        + _FRESH_SESSION_MARKERS
        + _ARCHITECTURE_MARKERS
        + _GOTCHA_MARKERS
        + _INDIRECT_HISTORY_MARKERS
        + _CONFLICT_MARKERS
    ):
        return "recall_or_report_unavailable"
    if any(marker in text for marker in _CONFLICT_MARKERS):
        return "recall_and_reconcile"
    if any(marker in text for marker in _NO_HIT_MARKERS):
        return "must_recall_then_no_hit"
    if any(
        marker in text
        for marker in _DECISION_MARKERS
        + _FRESH_SESSION_MARKERS
        + _ARCHITECTURE_MARKERS
        + _GOTCHA_MARKERS
        + _INDIRECT_HISTORY_MARKERS
    ):
        return "must_recall"
    if "refactor" in text or "alternatives" in text or "ambiguous" in text or "design choice" in text:
        return "may_recall"
    return "do_not_recall"


def render_policy(host: str = "generic", *, format: str = "markdown") -> RenderedPolicy:
    """Render the canonical policy through a thin host-specific adapter."""

    adapter = host_adapter(host)
    if format not in {"markdown", "json"}:
        raise ValueError("Policy format must be markdown or json.")
    normalized_host = adapter.host
    digest = policy_sha256()
    if format == "json":
        content = json.dumps(
            {
                "schema": POLICY_SCHEMA,
                "version": POLICY_VERSION,
                "policy_sha256": digest,
                "host": normalized_host,
                "adapter": adapter.as_dict(),
                "policy": canonical_policy(),
            },
            indent=2,
            ensure_ascii=False,
        )
    else:
        content = _render_markdown(adapter, digest)
    return RenderedPolicy(
        host=normalized_host,
        format=format,  # type: ignore[arg-type]
        content=content,
        policy_version=POLICY_VERSION,
        policy_sha256=digest,
        adapter=adapter,
    )


def _render_markdown(adapter: HostAdapter, digest: str) -> str:
    lines = [
        f"<!-- {POLICY_MARKER}:BEGIN version={POLICY_VERSION} sha256={digest} host={adapter.host} -->",
        "# AMB semantic memory use",
        "",
        f"Policy version: `{POLICY_VERSION}`",
        f"Policy SHA-256: `{digest}`",
        f"Host adapter: `{adapter.host}`",
        f"Instruction surface: `{adapter.instruction_surface}`",
        "",
        "This block decides when to consult the configured Agent Memory Bridge (AMB) MCP server. It does not require a recall for every turn, tool call, skill, or file edit.",
        "",
        "## Host placement",
        "",
        adapter.placement,
        adapter.loading_claim,
        "",
        "## MUST RECALL",
        "",
        *[f"- {rule.instruction}" for rule in MUST_RECALL_RULES],
        "",
        "## MAY RECALL",
        "",
        *[f"- {rule.instruction}" for rule in MAY_RECALL_RULES],
        "",
        "## DO NOT RECALL MERELY BECAUSE",
        "",
        *[f"- {rule.instruction}" for rule in DO_NOT_RECALL_RULES],
        "",
        "## Bounded procedure",
        "",
        "1. Classify the semantic project-history need; do not depend on literal words such as memory, recall, or remember.",
        "2. When recall is required or useful, make one bounded `recall` call in the relevant `project:<workspace>` namespace before the material decision or edit.",
        "3. Distinguish a successful no-hit from an unavailable/error route. Do not invent a project exception when recall returns no relevant memory.",
        "4. Check lifecycle, authority, relevance, and the current repository or configuration. Current live evidence wins over stale or conflicting history.",
        "5. If memory is used or rejected, report that fact with bounded evidence. A statement such as `I remember` is not invocation evidence.",
        "6. Store durable memory only after an explicit reviewed request. Do not automatically learn, write back, mutate prompts, or persist hidden reasoning.",
        "",
        "## Observable evidence",
        "",
        "Use existing `record_run_event` when a run is already being recorded: `memory_recalled` must use receipt-bound attribution; `memory_applied` or `memory_rejected` must link to the recall; use a bounded `observation` payload for policy, adapter, or tool-unavailable status. Never persist recall tokens, raw memory bodies, transcripts, or hidden reasoning.",
        "",
        "Classify failures as `policy_miss`, `host_miss`, `adapter_miss`, `tool_unavailable`, `retrieval_miss`, `memory_misuse`, `evidence_unavailable`, or `unsupported_memory_claim`.",
        f"<!-- {POLICY_MARKER}:END -->",
    ]
    return "\n".join(lines)


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"), sort_keys=True)
