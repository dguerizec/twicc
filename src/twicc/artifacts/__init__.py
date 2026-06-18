"""Web-facing resources for *served* artifacts.

The not-authenticated placeholder image and the standalone password page template
live here, not under ``twicc.auth``, because they are artifact-specific
presentation; the auth *mechanism* (verify / bind / rate-limit) stays in
``twicc.auth``. Both are plain committed files, so hatchling ships them inside the
wheel automatically (only gitignored build output needs the ``artifacts=`` list in
pyproject). The HTML page is a Django template, resolved via the ``TEMPLATES``
``DIRS`` entry pointing at ``templates/`` below.
"""

from pathlib import Path

_ASSETS_DIR = Path(__file__).resolve().parent

TEMPLATES_DIR = _ASSETS_DIR / "templates"

# Served inline (200) in place of a protected artifact image when the session is
# unauthenticated — an inline <img> can't follow an HTML redirect, so it would
# otherwise just show a broken image. Read once at import; no per-request I/O.
NOT_AUTHENTICATED_SVG = (_ASSETS_DIR / "not_authenticated.svg").read_text(encoding="utf-8")
