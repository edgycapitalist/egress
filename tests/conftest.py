"""Shared test fixtures and offline guards.

The test suite must run with no network access and no cloud credentials. The
agents reach Vertex AI only when an LLM call is actually made; merely *importing*
ADK / google-genai (which import ``google.auth.credentials`` at module load) does
not touch the network, and the baseline pipeline the tests exercise makes no LLM
call at all. So when the real ``google-auth`` is installed we use it — ADK needs
``google.auth`` to be a genuine package. Only when it is absent do we install a
package-shaped stub, so the suite still imports on a core-only environment.

The credential lookup ``google.auth.default()`` is always replaced with a mock, so
nothing here can reach Google's metadata server or read credentials from disk.
"""

import sys
from types import ModuleType
from unittest.mock import MagicMock


def _install_google_auth_stub() -> None:
    """Use the real google.auth if present; otherwise install a package-shaped fake."""
    try:
        import google.auth  # noqa: F401  (real package available — prefer it)
        import google.auth.credentials  # noqa: F401

        # Never let a test actually resolve credentials / hit the metadata server.
        google.auth.default = MagicMock(  # type: ignore[attr-defined]
            return_value=(MagicMock(name="credentials"), "test-project")
        )
        return
    except ImportError:
        pass

    google = sys.modules.get("google") or ModuleType("google")
    google.__path__ = getattr(google, "__path__", [])  # namespace package
    sys.modules["google"] = google

    auth = ModuleType("google.auth")
    auth.__path__ = []  # make it a package so `google.auth.credentials` imports
    auth.default = MagicMock(return_value=(MagicMock(name="credentials"), "test-project"))
    sys.modules["google.auth"] = auth
    google.auth = auth  # type: ignore[attr-defined]

    credentials = ModuleType("google.auth.credentials")
    credentials.Credentials = MagicMock(name="Credentials")
    sys.modules["google.auth.credentials"] = credentials
    auth.credentials = credentials  # type: ignore[attr-defined]


_install_google_auth_stub()
