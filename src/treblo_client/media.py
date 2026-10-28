"""Bounded, exclusive CDN transfers without forwarding authentication headers."""
from dataclasses import dataclass
import hashlib
import math
import os
from pathlib import Path
import struct
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import Request, build_opener

from .transport import _NoRedirect


@dataclass(frozen=True)
class Download:
    path: Path
    sha256: str
    size_bytes: int
    format: str
    duration_seconds: float | None = None


def _duration_and_format(header, size, format):
    header = bytes(header)
    valid = {
        "ogg": header.startswith(b"OggS"), "flac": header.startswith(b"fLaC"),
        "wav": header[:4] in {b"RIFF", b"RF64"} and header[8:12] == b"WAVE",
        "m4a": header[4:8] == b"ftyp",
        "mp3": header[:3] == b"ID3" or (len(header) >= 2 and header[0] == 255 and header[1] & 224 == 224),
    }
    if format not in valid or not valid[format]:
        raise ValueError("Downloaded media does not match the requested audio container")
    if format != "wav" or header[:4] != b"RIFF":
        return None
    # Parse bounded RIFF metadata without decoding the audio payload.
    offset, rate, data_size = 12, None, None
    while offset + 8 <= len(header):
        kind, length = header[offset:offset+4], struct.unpack_from("<I", header, offset+4)[0]
        if kind == b"fmt " and length >= 16 and offset + 24 <= len(header):
            rate = struct.unpack_from("<I", header, offset+16)[0]
        if kind == b"data":
            data_size = length
            if offset + 8 + length > size:
                raise ValueError("WAV payload is shorter than its declared data chunk")
            break
        offset += 8 + length + (length & 1)
    duration = data_size / rate if rate and data_size else None
    return duration if duration is not None and math.isfinite(duration) and duration > 0 else None


def stream_download(url, destination, *, format, max_bytes=256 * 1024 * 1024,
                    timeout=60, opener=None, before_publish=None):
    """Fetch one issued URL, hash and validate its container, then publish atomically.

    No retries or redirects; no cookies/JWT are forwarded to the CDN. Existing
    destinations are never replaced. Only a partial created by this call is
    removed after failure. No media decoder or browser process is involved.
    """
    if not isinstance(url, str):
        raise ValueError("Original conversion response omitted its audio URL")
    parsed = urlsplit(url)
    if (parsed.scheme != "https" or parsed.hostname not in {"cdn.treblo.com", "scdn.treblo.com"}
            or parsed.username or parsed.password or parsed.port not in {None, 443} or parsed.fragment):
        raise ValueError("Media URL is outside the selected HTTPS CDN origins")
    target = Path(destination).expanduser().absolute()
    if any(p.is_symlink() for p in (target, *target.parents)):
        raise ValueError("Download paths must not contain symbolic links")
    if target.exists():
        raise FileExistsError("Download destination already exists")
    if format not in {"wav", "mp3", "ogg", "flac", "m4a"}:
        raise ValueError("Unsupported audio format")
    if type(max_bytes) is not int or not 0 < max_bytes <= 1024 * 1024 * 1024:
        raise ValueError("Positive bounded media size required")
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_name(target.name + ".part")
    descriptor = os.open(partial, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as output:
            request = Request(url, headers={"User-Agent": "treblo-client/0.1.0", "Accept": "audio/*"})
            try:
                response = (opener or build_opener(_NoRedirect())).open(request, timeout=timeout)
            except HTTPError:
                raise RuntimeError("CDN refused the media request") from None
            with response:
                if response.code != 200:
                    raise RuntimeError("CDN did not return a complete media response")
                declared = response.headers.get("Content-Length")
                if declared is not None:
                    if not declared.isdigit() or not 0 < int(declared) <= max_bytes:
                        raise ValueError("CDN declared an invalid or excessive media size")
                digest, size, header = hashlib.sha256(), 0, bytearray()
                while block := response.read(min(1024 * 1024, max_bytes + 1 - size)):
                    size += len(block)
                    if size > max_bytes:
                        raise ValueError("Media response exceeded its byte limit")
                    digest.update(block)
                    if len(header) < 65536:
                        header.extend(block[:65536-len(header)])
                    output.write(block)
                if not size or (declared is not None and size != int(declared)):
                    raise ValueError("CDN media response was truncated")
                duration = _duration_and_format(header, size, format)
                output.flush()
                os.fsync(output.fileno())
        if before_publish is not None:
            before_publish(Download(partial, digest.hexdigest(), size, format, duration))
        os.link(partial, target)
        partial.unlink()
        directory = os.open(target.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
        return Download(target, digest.hexdigest(), size, format, duration)
    except BaseException:
        partial.unlink(missing_ok=True)
        raise
