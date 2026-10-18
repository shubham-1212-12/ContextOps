"""Test-only environment setup for the ContextOps application."""

import os


# Set credentials before any test module imports ``contextops.main``.  These
# values are intentionally confined to the test process; production code does
# not receive a fallback credential from this file.
os.environ["CONTEXTOPS_ADMIN_USERNAME"] = "contextops-test-admin"
os.environ["CONTEXTOPS_ADMIN_PASSWORD"] = "contextops-test-password"
