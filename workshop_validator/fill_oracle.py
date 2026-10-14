"""Fail-closed source-plan gate for literal form edits (synthetic-safe).

A successful Save click is not proof that the requested field contains the
literal value. Each authored fill must have a matching exact DOM_VALUE oracle.
This is structural plan validation, not proof that an actual UI behaved well.
"""


class FillOracleError(ValueError):
    """An authored fill lacks an independent value-sensitive postcondition."""


def require_literal_fill_oracles(step):
    """Reject an unobservable fill BEFORE private browser authentication.

    The required assertion must check the same accessible role and name,
    use DOM_VALUE with equality, and expect exactly the authored fill value.
    It may be among regular or necessity postconditions, but must be in the
    checkpoint's complete postcondition set (`step.assertions`).
    """
    for action in step.actions:
        if getattr(action, "kind", None) != "fill":
            continue
        if (not isinstance(action.value, str) or not action.role or
                not action.accessible_name):
            raise FillOracleError("Invalid literal fill action")
        if not any(
            assertion.role == action.role
            and assertion.accessible_name == action.accessible_name
            and assertion.spec.kind == "DOM_VALUE"
            and assertion.spec.operator == "eq"
            and assertion.spec.expected == action.value
            for assertion in step.assertions
        ):
            raise FillOracleError("Literal fill has no exact matching DOM_VALUE postcondition")
