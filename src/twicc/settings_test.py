"""Test settings for pytest-django."""

from twicc.settings import *  # noqa: F401, F403

# Use in-memory SQLite for tests
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": ":memory:",
    }
}

# Disable password protection in tests. The setting is otherwise sourced from
# the developer's local ``.env`` via :mod:`twicc.settings`, which would make
# every test that hits the HTTP stack require an authenticated session.
TWICC_PASSWORD_HASH = ""

# Disable logging during tests
LOGGING = {
    "version": 1,
    "disable_existing_loggers": True,
    "handlers": {},
    "loggers": {},
}

# Test compute version
CLAUDE_CODE_COMPUTE_VERSION = 99
