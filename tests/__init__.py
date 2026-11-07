"""Test suite initialization.

Sets WORKSHOP_VALIDATOR_CLAUDE to a non-executable path to prevent real claude launches.
"""
import os

# Prevent real claude launches during tests
if "WORKSHOP_VALIDATOR_CLAUDE" not in os.environ:
    os.environ["WORKSHOP_VALIDATOR_CLAUDE"] = "/nonexistent/claude"
