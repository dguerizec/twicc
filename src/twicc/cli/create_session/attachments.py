"""Validate and encode CLI attachments.

Validates by MIME (magic bytes), size, count, total size, and provider
support. Resizes images down to the provider/model-specific long-edge
cap (no upscaling, no resize if the image is already smaller). Produces
the SDK-format dicts the back-end expects in ``send_message`` payloads.
"""

from __future__ import annotations

import base64
import io
import os
from typing import NamedTuple


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
    file: str
    code: str
    message: str


class AttachmentSummaryItem(NamedTuple):
    """One row of the per-attachment summary printed after validation."""
    path: str
    kind: str                            # "image" | "document" | "text"
    mime: str
    original_size: int                   # bytes read from disk
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


def validate_and_encode(
    paths: list[str],
    support: dict,
    helpers,
    model: str | None,
) -> AttachmentResult:
    """Validate, resize, and base64-encode every attachment in order.

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

    if len(paths) > max_count:
        errors.append(AttachmentError(
            "<all>", "too_many", f"{len(paths)} attachments, max {max_count}",
        ))
        return AttachmentResult([], [], errors, [])

    # Pass 1: validate every file and accumulate (path, raw_bytes, mime, kind).
    # We need to know how many images there are to compute the right
    # resize cap (Anthropic tightens the cap for >20 images), so we
    # postpone resize/encode to a second pass.
    validated: list[tuple[str, bytes, str, str]] = []
    total = 0
    for path in paths:
        if not os.path.isfile(path):
            errors.append(AttachmentError(path, "not_a_file",
                                           f"file {path!r} does not exist"))
            continue
        size = os.path.getsize(path)
        if size > max_per_file:
            errors.append(AttachmentError(
                path, "size_exceeded",
                f"size {size / 1024 / 1024:.1f} MB exceeds "
                f"{max_per_file / 1024 / 1024:.0f} MB limit",
            ))
            continue
        total += size
        if total > max_total:
            errors.append(AttachmentError(
                path, "total_size_exceeded",
                f"total size exceeds {max_total / 1024 / 1024:.0f} MB",
            ))
            continue

        with open(path, "rb") as f:
            data = f.read()
        mime = _sniff_mime(data)
        if mime is None or mime not in accepted:
            accepted_list = ", ".join(sorted(accepted))
            errors.append(AttachmentError(
                path, "unsupported_mime",
                f"type {mime or 'unknown'} not supported "
                f"(accepted: {accepted_list})",
            ))
            continue

        validated.append((path, data, mime, _kind_of(mime)))

    if errors:
        return AttachmentResult([], [], errors, [])

    # Determine the long-edge cap once, using the resolved provider helper
    # and the image count we just observed.
    num_images = sum(1 for _, _, _, kind in validated if kind == "image")
    target_dim = helpers.get_effective_image_dimension(model, num_images)

    # Pass 2: resize images, base64-encode binaries, gather summary.
    for path, data, mime, kind in validated:
        original_size = len(data)
        original_dim: tuple[int, int] | None = None
        final_dim: tuple[int, int] | None = None
        resized = False

        if kind == "image":
            data, mime, original_dim, final_dim, resized = resize_image_if_needed(
                data, mime, target_dim, path=path,
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
            path=path,
            kind=kind,
            mime=mime,
            original_size=original_size,
            final_size=len(data),
            original_dim=original_dim,
            final_dim=final_dim,
            resized=resized,
        ))

    return AttachmentResult(images, documents, [], summary)
