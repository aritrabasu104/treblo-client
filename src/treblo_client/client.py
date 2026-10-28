"""Treblo API operations; native owners supply dispatch intents and transport."""

from .protocol import Request, WEB_ORIGIN, GenerationV3, current_user, project, track, download_format
from .transport import Transport, require_success, HttpTransport
from .auth import Session, SessionTransport
from .protocol import _identifier
from urllib.parse import urlencode


class TrebloClient:
    def __init__(self, transport: Transport | Session):
        self.transport = (SessionTransport(HttpTransport(), transport)
                          if isinstance(transport, Session) else transport)

    def _execute(self, request):
        return require_success(self.transport.execute(request))

    def current_user(self):
        return self._execute(current_user())

    def project(self, project_id):
        return self._execute(project(project_id))

    def track(self, track_id):
        return self._execute(track(track_id))

    def project_tracks(self, project_id, *, cursor=None):
        url = WEB_ORIGIN + "/api/v1/projects/" + _identifier(project_id) + "/tracks"
        if cursor is not None:
            if not isinstance(cursor, str) or not 0 < len(cursor) <= 4096:
                raise ValueError("Original bounded pagination cursor required")
            url += "?" + urlencode({"cursor": cursor})
        return self._execute(Request("GET", url, headers={"X-Treblo-Platform": "web"}))

    def create_project(self, title, *, before_dispatch=None):
        request = Request("POST", WEB_ORIGIN + "/api/v1/projects", {"title": title},
                          {"X-Treblo-Platform": "web"})
        if before_dispatch is not None:
            before_dispatch(request)
        return self._execute(request)

    def generate(self, configuration: GenerationV3, project_id, *, access_token,
                 attempt_id, client_version, before_dispatch=None):
        request = configuration.prepare(project_id, access_token=access_token,
                                        attempt_id=attempt_id, client_version=client_version)
        if before_dispatch is not None:
            before_dispatch(request)
        return self._execute(request)

    def download(self, track_id, format, *, access_token, before_dispatch=None):
        request = download_format(track_id, format, access_token=access_token)
        if before_dispatch is not None:
            before_dispatch(request)
        return self._execute(request)

    def download_file(self, track_id, destination, *, format="wav", before_dispatch=None,
                      converted_url=None):
        """Convert once, then stream the issued URL to an exclusive local file.

        Supply ``converted_url`` only when resuming a previously recorded
        conversion. Its secure custody and any retry decision belong to caller.
        Track identity is supplied by the caller's exact metadata readback.
        """
        from .media import stream_download
        session_transport = self.transport
        if not isinstance(session_transport, SessionTransport):
            raise ValueError("download_file requires an authenticated SessionTransport")
        if converted_url is None:
            converted = self.download(track_id, format,
                access_token=session_transport.session.access_token,
                before_dispatch=before_dispatch)
            converted_url = converted.get("audio_url") if isinstance(converted, dict) else None
        return stream_download(converted_url, destination, format=format)
