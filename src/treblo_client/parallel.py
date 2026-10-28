"""Bounded foreground batches. All dispatched results remain visible to callers."""
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .media import Download
from .protocol import _identifier


@dataclass(frozen=True)
class DownloadRequest:
    track_id: str
    destination: str | Path
    format: str = "wav"
    before_dispatch: Callable | None = field(default=None, repr=False, compare=False)
    converted_url: str | None = field(default=None, repr=False, compare=False)


@dataclass(frozen=True)
class DownloadResult:
    request: DownloadRequest
    download: Download | None = None
    error: Exception | None = field(default=None, repr=False)
    skipped: bool = False


def download_many(manager, requests, *, max_workers=10) -> list[DownloadResult]:
    """Download independent tracks concurrently, returning results in input order.

    Stop admitting work after any failure, settle already dispatched siblings,
    and mark the unstarted tail skipped. Never retry conversions or transfers.
    Durable conversion intents and issued URLs remain the caller's responsibility.
    """
    if type(max_workers) is not int or not 1 <= max_workers <= 32:
        raise ValueError("max_workers must be an integer from 1 to 32")
    requests = list(requests)
    destinations, tracks = set(), set()
    for request in requests:
        if not isinstance(request, DownloadRequest):
            raise ValueError("Expected DownloadRequest objects")
        _identifier(request.track_id)
        if request.format not in {"wav", "mp3", "flac", "m4a", "ogg"}:
            raise ValueError("Unsupported audio format")
        target = Path(request.destination).expanduser().absolute()
        if any(path.is_symlink() for path in (target, *target.parents)):
            raise ValueError("Download paths must not contain symbolic links")
        target = target.resolve()
        identity = (request.track_id, request.format)
        if target in destinations or identity in tracks:
            raise ValueError("A batch requires distinct tracks/formats and destination paths")
        # Reject known local collisions before any conversion is dispatched.
        partial = target.with_name(target.name + ".part")
        if target.exists() or partial.exists() or partial.is_symlink():
            raise FileExistsError("Download destination or partial already exists")
        destinations.add(target); tracks.add(identity)
    results = [DownloadResult(request, skipped=True) for request in requests]
    def run(request):
        return manager.download_file(request.track_id, request.destination,
            format=request.format, before_dispatch=request.before_dispatch,
            converted_url=request.converted_url)
    with ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="treblo-http") as pool:
        pending, next_index, stopped = {}, 0, False
        while pending or (next_index < len(requests) and not stopped):
            while not stopped and next_index < len(requests) and len(pending) < max_workers:
                pending[pool.submit(run, requests[next_index])] = next_index
                next_index += 1
            done, _ = wait(pending, return_when=FIRST_COMPLETED)
            for future in done:
                index = pending.pop(future)
                try:
                    results[index] = DownloadResult(requests[index], download=future.result())
                except Exception as error:
                    results[index] = DownloadResult(requests[index], error=error)
                    stopped = True
    return results
