"""Importable client for Treblo authentication, metadata and audio downloads."""
from .auth import AuthClient, OAuthAttempt, Session, SessionTransport
from .client import TrebloClient
from .media import Download, stream_download, MediaAccessRefused, MediaTransferError
from .metadata import probe_audio
from .protocol import GenerationV3
from .transport import AccessRefused, ApiError, HttpTransport, OutcomeUnknown
from .sessions import (SessionManager, SessionStore, SessionRecord, MemorySessionStore,
                       FileSessionLock, SessionUnavailable)
from .parallel import DownloadRequest, DownloadResult, download_many

__version__ = "0.2.0"
__all__ = ["AuthClient", "OAuthAttempt", "Session", "SessionTransport", "TrebloClient",
           "GenerationV3", "Download", "stream_download", "HttpTransport", "ApiError",
           "AccessRefused", "OutcomeUnknown", "probe_audio", "MediaAccessRefused", "MediaTransferError",
           "SessionManager", "SessionStore", "SessionRecord", "MemorySessionStore", "FileSessionLock",
           "SessionUnavailable", "DownloadRequest", "DownloadResult", "download_many"]
