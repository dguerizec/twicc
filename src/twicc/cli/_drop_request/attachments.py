"""Validate and encode CLI attachments.

Validates by MIME (magic bytes), size, count, total size, and provider
support. Resizes images down to the provider/model-specific long-edge
cap (no upscaling, no resize if the image is already smaller). Produces
the SDK-format dicts the back-end expects in ``send_message`` payloads.
"""

from __future__ import annotations

import base64
import binascii
import io
import os
from typing import NamedTuple

from twicc.cli._output import in_api_mode


# Magic byte signatures for each accepted binary type.
_MAGIC = [
    ("image/png",       b"\x89PNG\r\n\x1a\n"),
    ("image/jpeg",      b"\xff\xd8\xff"),
    ("image/gif",       b"GIF87a"),
    ("image/gif",       b"GIF89a"),
    ("image/webp",      b"RIFF"),       # plus "WEBP" at offset 8
    ("application/pdf", b"%PDF-"),
]


class AttachmentError(NamedTuple):
    # ``file`` may hold a filesystem path OR a ``data:<mime>`` label for data-URI inputs.
    file: str
    code: str
    message: str


class AttachmentSummaryItem(NamedTuple):
    """One row of the per-attachment summary printed after validation."""
    # ``path`` may hold a filesystem path OR a ``data:<mime>`` label for data-URI inputs.
    path: str
    kind: str                            # "image" | "document" | "text"
    mime: str
    original_size: int                   # bytes (from disk or decoded URI)
    final_size: int                      # bytes after resize (== original for non-images / no-op)
    original_dim: tuple[int, int] | None  # (w, h) before resize, images only
    final_dim: tuple[int, int] | None     # (w, h) after resize, images only
    resized: bool


class AttachmentResult(NamedTuple):
    images: list[dict]
    documents: list[dict]
    errors: list[AttachmentError]
    summary: list[AttachmentSummaryItem]


class AttachmentResizeError(Exception):
    """Raised when Pillow fails to open or resize an image.

    The command layer translates this to a validation-style error frame
    and aborts (no partial batch).
    """

    def __init__(self, path: str, message: str) -> None:
        self.path = path
        self.message = message
        super().__init__(f"resize failed for {path}: {message}")


def _sniff_mime(data: bytes) -> str | None:
    for mime, magic in _MAGIC:
        if mime == "image/webp":
            if data.startswith(magic) and data[8:12] == b"WEBP":
                return mime
        elif data.startswith(magic):
            return mime
    # Fallback: if decodable as UTF-8, treat as text/plain.
    try:
        data.decode("utf-8")
        return "text/plain"
    except UnicodeDecodeError:
        return None


def _parse_data_uri(spec: str) -> tuple[str, bytes]:
    """Decode a base64 data URI ``data:<mediatype>;base64,<data>`` → (declared_mime, raw_bytes).

    The declared media type is returned only for a display label; the real
    type is re-sniffed from the bytes downstream (so a wrong declared type
    cannot bypass validation). Raises ValueError on a malformed / non-base64
    data URI.
    """
    rest = spec[len("data:"):]
    header, sep, b64 = rest.partition(",")
    if not sep:
        raise ValueError("malformed data URI (missing comma)")
    params = header.split(";")
    declared_mime = params[0].strip() or "application/octet-stream"
    if not any(p.strip().lower() == "base64" for p in params[1:]):
        raise ValueError("only base64 data URIs are supported (data:<mime>;base64,...)")
    try:
        raw = base64.b64decode("".join(b64.split()), validate=True)
    except (binascii.Error, ValueError):
        raise ValueError("invalid base64 payload in data URI")
    if not raw:
        raise ValueError("empty data URI payload")
    return declared_mime, raw


def resize_image_if_needed(
    data: bytes,
    mime: str,
    max_dim: int,
    *,
    path: str,
) -> tuple[bytes, str, tuple[int, int], tuple[int, int], bool]:
    """Cap the long edge of ``data`` to ``max_dim`` (no upscaling).

    Returns ``(out_bytes, out_mime, original_dim, final_dim, resized)``.

    Mirrors ``resizeImageIfNeeded`` in ``frontend/src/utils/fileUtils.js``:
    JPEG stays JPEG (quality 92), everything else becomes PNG (lossless,
    ideal for screenshots). Aspect ratio preserved. Raises
    :class:`AttachmentResizeError` on any failure — caller aborts.
    """
    # Local import so the rest of the module remains importable without
    # Pillow installed (the CLI does require it for any image attach).
    try:
        from PIL import Image, UnidentifiedImageError
    except ImportError as e:  # pragma: no cover — Pillow listed in pyproject.
        raise AttachmentResizeError(
            path, f"Pillow is not installed: {e}. Run `uv add pillow`."
        )

    try:
        img = Image.open(io.BytesIO(data))
        img.load()  # force-decode to surface format/decode errors here
    except (UnidentifiedImageError, OSError) as e:
        raise AttachmentResizeError(path, f"cannot read image: {e}")

    width, height = img.size
    original_dim = (width, height)
    if width <= max_dim and height <= max_dim:
        return data, mime, original_dim, original_dim, False

    scale = min(max_dim / width, max_dim / height)
    new_w = max(1, round(width * scale))
    new_h = max(1, round(height * scale))

    try:
        resized = img.resize((new_w, new_h), Image.LANCZOS)
    except Exception as e:  # pragma: no cover — Pillow rarely fails here
        raise AttachmentResizeError(path, f"resize failed: {e}")

    # JPEG in → JPEG out; everything else → PNG (lossless).
    if mime == "image/jpeg":
        out_mime = "image/jpeg"
        out = io.BytesIO()
        # Convert away from RGBA / P modes that JPEG can't encode.
        if resized.mode not in ("RGB", "L"):
            resized = resized.convert("RGB")
        try:
            resized.save(out, format="JPEG", quality=92, optimize=True)
        except Exception as e:
            raise AttachmentResizeError(path, f"JPEG encode failed: {e}")
    else:
        out_mime = "image/png"
        out = io.BytesIO()
        try:
            resized.save(out, format="PNG", optimize=True)
        except Exception as e:
            raise AttachmentResizeError(path, f"PNG encode failed: {e}")

    return out.getvalue(), out_mime, original_dim, (new_w, new_h), True


def _kind_of(mime: str) -> str:
    if mime.startswith("image/"):
        return "image"
    if mime == "application/pdf":
        return "document"
    return "text"


def _size_error(label: str, size: int, max_per_file: int) -> AttachmentError:
    return AttachmentError(
        label, "size_exceeded",
        f"size {size / 1024 / 1024:.1f} MB exceeds "
        f"{max_per_file / 1024 / 1024:.0f} MB limit",
    )


def _resolve_spec(spec: str, max_per_file: int) -> tuple[str, bytes] | AttachmentError:
    """Resolve one --attach entry (file path OR data: URI) to (label, raw_bytes).

    Returns an AttachmentError instead on a bad data URI, a missing file, or a
    per-file size overflow. For filesystem inputs the size is checked via
    os.path.getsize() BEFORE the file is read, so an oversized file is rejected
    without being loaded into memory.
    """
    if spec.startswith("data:"):
        try:
            declared_mime, data = _parse_data_uri(spec)
        except ValueError as e:
            return AttachmentError(f"data:{spec[5:40]}…", "invalid_data_uri", str(e))
        label = f"data:{declared_mime}"
        if len(data) > max_per_file:
            return _size_error(label, len(data), max_per_file)
        return label, data

    label = spec
    if in_api_mode() and not os.path.isabs(spec):
        return AttachmentError(
            spec, "relative_path",
            "relative path not allowed over the API (no caller working directory); "
            "pass an absolute path or a data: URI",
        )
    if not os.path.isfile(spec):
        return AttachmentError(spec, "not_a_file", f"file {spec!r} does not exist")
    size = os.path.getsize(spec)
    if size > max_per_file:
        return _size_error(label, size, max_per_file)
    with open(spec, "rb") as f:
        return label, f.read()


def validate_and_encode(
    specs: list[str],
    support: dict,
    helpers,
    model: str | None,
) -> AttachmentResult:
    """Validate, resize, and base64-encode every attachment in order.

    Each entry in ``specs`` is either a local file path or a base64 data URI
    of the form ``data:<mime>;base64,<data>``. The data-URI form lets
    remote/API callers attach files without a shared filesystem.

    Validation errors (size / count / total / unsupported MIME) are
    aggregated and returned in ``AttachmentResult.errors``. The caller
    is expected to abort when ``errors`` is non-empty.

    Resize errors propagate as :class:`AttachmentResizeError` and the
    caller must abort — there is no partial-success path for them.
    """
    images: list[dict] = []
    documents: list[dict] = []
    errors: list[AttachmentError] = []
    summary: list[AttachmentSummaryItem] = []

    accepted = set(support.get("accepted_mime_types", []))
    max_per_file = support.get("max_bytes_per_file") or 0
    max_total = support.get("max_total_bytes") or 0
    max_count = support.get("max_files_per_message") or 0

    if len(specs) > max_count:
        errors.append(AttachmentError(
            "<all>", "too_many", f"{len(specs)} attachments, max {max_count}",
        ))
        return AttachmentResult([], [], errors, [])

    # Pass 1: validate every entry (file path or data URI) and accumulate
    # (label, raw_bytes, mime, kind). ``label`` is the path for filesystem
    # inputs and ``data:<mime>`` for data-URI inputs — used in error messages
    # and summary output only.
    # We need to know how many images there are to compute the right
    # resize cap (Anthropic tightens the cap for >20 images), so we
    # postpone resize/encode to a second pass.
    validated: list[tuple[str, bytes, str, str]] = []
    total = 0
    for spec in specs:
        resolved = _resolve_spec(spec, max_per_file)
        if isinstance(resolved, AttachmentError):
            errors.append(resolved)
            continue
        label, data = resolved
        total += len(data)
        if total > max_total:
            errors.append(AttachmentError(
                label, "total_size_exceeded",
                f"total size exceeds {max_total / 1024 / 1024:.0f} MB",
            ))
            continue
        mime = _sniff_mime(data)
        if mime is None or mime not in accepted:
            accepted_list = ", ".join(sorted(accepted))
            errors.append(AttachmentError(
                label, "unsupported_mime",
                f"type {mime or 'unknown'} not supported "
                f"(accepted: {accepted_list})",
            ))
            continue
        validated.append((label, data, mime, _kind_of(mime)))

    if errors:
        return AttachmentResult([], [], errors, [])

    # Determine the long-edge cap once, using the resolved provider helper
    # and the image count we just observed.
    num_images = sum(1 for _, _, _, kind in validated if kind == "image")
    target_dim = helpers.get_effective_image_dimension(model, num_images)

    # Pass 2: resize images, base64-encode binaries, gather summary.
    for label, data, mime, kind in validated:
        original_size = len(data)
        original_dim: tuple[int, int] | None = None
        final_dim: tuple[int, int] | None = None
        resized = False

        if kind == "image":
            data, mime, original_dim, final_dim, resized = resize_image_if_needed(
                data, mime, target_dim, path=label,
            )
            images.append({
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": mime,
                    "data": base64.b64encode(data).decode("ascii"),
                },
            })
        elif kind == "document":  # application/pdf
            documents.append({
                "type": "document",
                "source": {
                    "type": "base64",
                    "media_type": "application/pdf",
                    "data": base64.b64encode(data).decode("ascii"),
                },
            })
        else:  # text/plain
            documents.append({
                "type": "document",
                "source": {
                    "type": "text",
                    "media_type": "text/plain",
                    "data": data.decode("utf-8"),
                },
            })

        summary.append(AttachmentSummaryItem(
            path=label,
            kind=kind,
            mime=mime,
            original_size=original_size,
            final_size=len(data),
            original_dim=original_dim,
            final_dim=final_dim,
            resized=resized,
        ))

    return AttachmentResult(images, documents, [], summary)
