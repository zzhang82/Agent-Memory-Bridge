from __future__ import annotations

from dataclasses import dataclass

CONTRADICTION_MARKERS = (
    "however",
    "but ",
    "except ",
    "unless ",
)

NON_CONTRADICTORY_BOUNDARY_CUE_GROUPS = (
    ("fallback", "default"),
    ("core policy", "manual"),
    ("core-policy", "manual"),
    ("project memory", "global core"),
)

BOUNDARY_SCOPE_VERBS = (
    "keep ",
    "remain ",
    "remains ",
    "stay ",
    "stays ",
)

STRONG_CONTRADICTION_CUES = (
    "not a license",
    "automate policy",
    "auto-policy",
    "risky",
    " risk ",
    "breaks",
    " break ",
)


@dataclass(frozen=True, slots=True)
class ContradictionAssessment:
    counts_as_contradiction: bool
    reason_code: str


BOUNDARY_REASON_CODES = {
    ("fallback", "default"): "boundary-exempt:fallback-default",
    ("core policy", "manual"): "boundary-exempt:manual-policy",
    ("core-policy", "manual"): "boundary-exempt:manual-policy",
    ("project memory", "global core"): "boundary-exempt:project-vs-global",
}


def assess_contradiction_claim(claim: str) -> ContradictionAssessment:
    normalized = normalize_contradiction_text(claim)
    if not any(marker in normalized for marker in CONTRADICTION_MARKERS):
        return ContradictionAssessment(False, "no-marker")
    if any(cue in normalized for cue in STRONG_CONTRADICTION_CUES):
        return ContradictionAssessment(True, "strong-cue")
    boundary_reason = boundary_only_scope_reason(normalized)
    if boundary_reason is not None:
        return ContradictionAssessment(False, boundary_reason)
    return ContradictionAssessment(True, "marker-contrast")


def claim_counts_as_contradiction(claim: str) -> bool:
    return assess_contradiction_claim(claim).counts_as_contradiction


def is_boundary_only_scope_contrast(normalized_claim: str) -> bool:
    return boundary_only_scope_reason(normalized_claim) is not None


def boundary_only_scope_reason(normalized_claim: str) -> str | None:
    if not any(verb in normalized_claim for verb in BOUNDARY_SCOPE_VERBS):
        return None
    for cue_group in NON_CONTRADICTORY_BOUNDARY_CUE_GROUPS:
        if all(cue in normalized_claim for cue in cue_group):
            return BOUNDARY_REASON_CODES[cue_group]
    return None


def normalize_contradiction_text(text: str) -> str:
    return " ".join(text.lower().split())
