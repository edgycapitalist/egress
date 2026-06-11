"""Shared test fixtures and offline guards.

The test suite must run with no network access and no cloud credentials. We stub
`google.auth` (and the ADC lookup it performs) so importing any agent module in a
test never reaches Google's metadata server or the filesystem for credentials.
Feature tests are added with their phases; this file keeps the suite offline from
day one.
"""

import sys
from types import ModuleType
from unittest.mock import MagicMock


def _install_google_auth_stub() -> None:
    """Insert a fake google.auth into sys.modules if the real one is absent.

    Keeps imports cheap and offline. Real integration tests against Vertex AI run
    separately, outside this offline suite.
    """
    if "google.auth" in sys.modules:
        return

    google = sys.modules.get("google") or ModuleType("google")
    google.__path__ = getattr(google, "__path__", [])  # mark as a namespace package
    sys.modules["google"] = google

    auth = ModuleType("google.auth")
    auth.default = MagicMock(return_value=(MagicMock(name="credentials"), "test-project"))
    sys.modules["google.auth"] = auth
    google.auth = auth  # type: ignore[attr-defined]


_install_google_auth_stub()
