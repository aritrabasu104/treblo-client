"""Importable client for Treblo authentication, metadata and audio downloads."""
from .auth import AuthClient, OAuthAttempt, Session, SessionTransport
from .client import TrebloClient
from .media import Download, stream_download, MediaAccessRefused, MediaTransferError
from .metadata import probe_audio
from .protocol import GenerationV3
from .verification import GenerationChallenge, VerificationProof, VerificationDispatch
from .transport import AccessRefused, ApiError, HttpTransport, OutcomeUnknown, TransportUnavailable, VerificationRequired
from .sessions import (SessionManager, SessionStore, SessionRecord, MemorySessionStore,
                       FileSessionLock, SessionUnavailable, LoginRequired)
from .parallel import DownloadRequest, DownloadResult, download_many

__version__ = "0.3.3"
__all__ = ["AuthClient", "OAuthAttempt", "Session", "SessionTransport", "TrebloClient",
           "GenerationV3", "GenerationChallenge", "VerificationProof", "VerificationDispatch",
           "Download", "stream_download", "HttpTransport", "ApiError",
           "AccessRefused", "VerificationRequired", "OutcomeUnknown", "TransportUnavailable", "probe_audio", "MediaAccessRefused", "MediaTransferError",
           "SessionManager", "SessionStore", "SessionRecord", "MemorySessionStore", "FileSessionLock",
           "SessionUnavailable", "LoginRequired", "DownloadRequest", "DownloadResult", "download_many"]
