# Exact-value oracle preflight for literal form edits

Status: public-safe synthetic gate; **no live Salesforce verification**.

An authored Playwright `fill` can succeed and a Save button can become visible
while the wrong value remains in the field. Before any private login, the
source-reviewed executable plan now requires each `fill` action to have a
corresponding **exact** `DOM_VALUE eq` postcondition with the same accessible
role/name and expected value equal to the literal authored input. This is
checked against the same `step.assertions` frozen by the independent
checkpoint verifier. A Save-only, unrelated-control, `DOM_TEXT`, or substring
oracle does not qualify. A multi-field action needs one exact oracle per field.

The synthetic Chromium negative control intentionally saves a wrong value:
the Save-only assertion PASSes, while the exact field-value assertion returns
`FUNCTIONAL_FAIL`. The pre-login plan gate prevents using that weak Save-only
oracle for a private fill step. It does **not** prove a current Salesforce UI
defect, authenticate browser evidence, or establish downstream correctness.
Additional downstream checks are still required for meaningful guide claims.

For non-input rich editors where Playwright `fill` works but `input_value()`
does not, the operator must first author a supported exact-value observation
and extend/test this gate, rather than bypassing it or using a weak visible
button as proof. Necessity probes, reset/skip-only-one-action experiments and
fault restoration remain separately controlled. No guide-removal recommendation
is permitted from a preflight pass alone.

The private operator still needs approved runner/storage, independently pinned
reviewed plan, rotated secrets, reproducible disposable baseline, fresh
before-state evidence and authorized negative controls. Both private outputs
(quick HTML and pixel-preserving annotated instructor PDF) remain mandatory.
