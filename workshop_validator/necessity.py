"""Fail-closed analysis of whether a guide-directed manual edit is still needed.

This evaluates independent evidence from controlled, safely reversible trials.
It never edits a source guide, auto-skips a step, or grants a browser PASS.
A redundancy finding is only an instructor-review CANDIDATE, not proof of a
product-wide obsolete instruction.
"""

from dataclasses import dataclass

from .evidence import FUNCTIONAL_FAIL, NEEDS_REVIEW, PASS, Verification


@dataclass(frozen=True)
class NecessityFinding:
    classification: str
    propose_guide_removal: bool
    requires_instructor_review: bool
    reasons: tuple[str, ...]


def assess_necessity(
    before: Verification,
    *,
    after_skipping: Verification | None = None,
    after_executing: Verification | None = None,
    negative_control: Verification | None = None,
    baseline_reproduced: bool = False,
    prerequisites_verified: bool = False,
) -> NecessityFinding:
    """Classify a manual instruction from its behavior, not PDF numbering.

    Inputs are prior independent verifier results:
      - before: was the intended state already true before this instruction?
      - after_skipping: did required downstream checks PASS when intentionally
        omitting only this instruction from an approved disposable-org baseline?
      - after_executing: did a fresh/replayed run PASS after executing it?
      - negative_control: did a safe targeted break of the asserted property
        produce FUNCTIONAL_FAIL (showing that the check is sensitive)?

    The caller must prove matching source, state, baseline and independence.
    This structural prototype cannot verify those claims cryptographically.
    """
    candidates = (before, after_skipping, after_executing, negative_control)
    if any(x is not None and x.status not in {PASS, FUNCTIONAL_FAIL, NEEDS_REVIEW}
           for x in candidates):
        raise ValueError("Only independent verifier outcomes are accepted")

    def result(name: str, *reasons: str, proposed: bool = False):
        return NecessityFinding(name, proposed, True, tuple(reasons))

    if negative_control is not None and negative_control.status == PASS:
        return result("TEST_ORACLE_INSENSITIVE",
                      "An intentionally broken state still passed an assertion.",
                      "Do not conclude that the manual instruction is unnecessary.")

    if before.status == NEEDS_REVIEW:
        return result("INSUFFICIENT_PRECONDITION_EVIDENCE",
                      "Missing or invalid before-state evidence.")

    if before.status == FUNCTIONAL_FAIL:
        if after_executing is not None and after_executing.status == PASS:
            return result("ACTION_HELPED_ON_TESTED_BASELINE",
                          "Required state was absent before this action and present after it.",
                          "This does not establish behavior in other org versions.")
        if after_executing is not None and after_executing.status == FUNCTIONAL_FAIL:
            return result("ACTION_DID_NOT_FIX_RESULT",
                          "The manual action did not satisfy its expected result.")
        return result("ACTION_NECESSITY_UNTESTED",
                      "The target state is absent; execute the guide-directed action and verify it.")

    if not baseline_reproduced or not prerequisites_verified:
        return result("PRECONDITION_ALREADY_MET_UNPROVEN",
                      "The state was already correct but prerequisites or clean-baseline reproduction are unverified.")
    if after_skipping is None:
        return result("SKIP_PATH_NOT_TESTED",
                      "The precondition is already satisfied, but downstream behavior without this step is untested.")
    if after_skipping.status == NEEDS_REVIEW:
        return result("SKIP_PATH_INCONCLUSIVE",
                      "Downstream observations after skipping the instruction were invalid or missing.")
    if after_skipping.status == FUNCTIONAL_FAIL:
        return result("SKIP_PATH_FAILED",
                      "The instruction may have necessary side effects even if its visible output already exists.")
    if negative_control is None or negative_control.status == NEEDS_REVIEW:
        return result("NEEDS_NEGATIVE_CONTROL",
                      "Skipping passed on this baseline, but assertion sensitivity was not proven.")

    return result("REDUNDANCY_CANDIDATE",
                  "The property was satisfied before the edit on a reproduced clean baseline.",
                  "Downstream checks passed after safely skipping the edit.",
                  "A deliberately broken property was detected by the same assertions.",
                  "Instructor approval and repeatability review are still required.",
                  proposed=True)
