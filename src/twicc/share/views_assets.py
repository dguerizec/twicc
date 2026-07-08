"""Serve the built share-session bundle from ``static/share-session/`` under the
public ``/_twicc/share/`` prefix. Clone of ``views.artifact_shell_asset``."""

from __future__ import annotations

import asyncio
import os

from django.conf import settings
from django.http import Http404, HttpResponseNotAllowed

from twicc.views import _raw_file_response


async def share_asset(request, asset):
    if request.method not in ("GET", "HEAD"):
        return HttpResponseNotAllowed(["GET", "HEAD"])
    base = (settings.PACKAGE_DIR / "static" / "share-session").resolve()
    target = (base / asset).resolve()
    if not str(target).startswith(str(base) + os.sep):
        raise Http404("Not found")
    response = await asyncio.to_thread(_raw_file_response, str(target))
    if response is None:
        raise Http404("Share bundle not built")
    return response
