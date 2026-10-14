"""Synthetic-only regression for pre-login value-sensitive fill oracles."""
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from workshop_validator.fill_oracle import FillOracleError, require_literal_fill_oracles
from workshop_validator.private_preflight import (
    SourcePreflightError, require_executable_source_review,
)


def action(kind="fill", value="list[object]", role="textbox", name="Generated script"):
    return SimpleNamespace(kind=kind, value=value, role=role, accessible_name=name)


def assertion(kind="DOM_VALUE", operator="eq", expected="list[object]",
              role="textbox", name="Generated script"):
    return SimpleNamespace(
        spec=SimpleNamespace(kind=kind, operator=operator, expected=expected),
        role=role, accessible_name=name,
    )


def step(actions=None, assertions=None):
    return SimpleNamespace(
        step_id="synthetic-step", page=1, required=True, dependencies=(),
        actions=actions if actions is not None else (action(),),
        assertions=assertions if assertions is not None else (assertion(),),
    )


class LiteralFillOracleTests(unittest.TestCase):
    def test_exact_field_value_accepted(self):
        require_literal_fill_oracles(step())

    def test_save_button_only_rejected(self):
        with self.assertRaises(FillOracleError):
            require_literal_fill_oracles(step(assertions=(
                assertion(kind="DOM_VISIBLE", expected=True, role="button", name="Save"),)))

    def test_wrong_field_and_wrong_value_rejected(self):
        for candidate in (assertion(name="Other field"), assertion(expected="string")):
            with self.subTest(candidate=candidate), self.assertRaises(FillOracleError):
                require_literal_fill_oracles(step(assertions=(candidate,)))

    def test_substring_and_text_not_value_rejected(self):
        for candidate in (assertion(operator="contains"), assertion(kind="DOM_TEXT")):
            with self.subTest(candidate=candidate), self.assertRaises(FillOracleError):
                require_literal_fill_oracles(step(assertions=(candidate,)))

    def test_each_fill_needs_own_oracle(self):
        with self.assertRaises(FillOracleError):
            require_literal_fill_oracles(step(actions=(
                action(), action(value="wrong", name="Other field"))))
        require_literal_fill_oracles(step(
            actions=(action(), action(value="wrong", name="Other field")),
            assertions=(assertion(), assertion(expected="wrong", name="Other field"))))

    def test_click_only_unchanged(self):
        require_literal_fill_oracles(step(
            actions=(action(kind="click"),),
            assertions=(assertion(kind="DOM_VISIBLE", expected=True),)))

    def test_malformed_fill_rejected(self):
        for candidate in (action(value=None), action(role=""), action(name="")):
            with self.subTest(candidate=candidate), self.assertRaises(FillOracleError):
                require_literal_fill_oracles(step(actions=(candidate,)))

    def test_private_preflight_blocks_weak_oracle_before_login(self):
        inventory = SimpleNamespace(steps=(SimpleNamespace(
            step_id="synthetic-step", page=1),))
        review = SimpleNamespace(pages=(SimpleNamespace(
            page=1, candidates=(SimpleNamespace(
                step_id="synthetic-step", disposition="ACTION"),),
            added_actions=()),))
        with patch("workshop_validator.private_preflight.reconcile_inventory",
                   return_value=SimpleNamespace(complete=True,
                                                action_ids=("synthetic-step",))):
            self.assertEqual(require_executable_source_review(
                inventory, review, "synthetic-digest", (step(),)),
                ("synthetic-step",))
            with self.assertRaises(SourcePreflightError):
                require_executable_source_review(inventory, review,
                    "synthetic-digest", (step(assertions=(
                        assertion(kind="DOM_VISIBLE", expected=True,
                                  role="button", name="Save"),)),))


if __name__ == "__main__":
    unittest.main()
