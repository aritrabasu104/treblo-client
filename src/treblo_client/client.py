"""Treblo API operations; native owners supply dispatch intents and transport."""

from .protocol import Request, WEB_ORIGIN, GenerationV3, current_user, project, track, download_format
from .transport import Transport, require_success, HttpTransport, VerificationRequired
from .verification import GenerationChallenge, VerificationProof
from dataclasses import replace
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

    def owned_project_tracks(self, project_id, *, artist_id, max_pages=4):
        """Read a complete bounded project in the service's display order.

        Preserve track IDs and order; callers retain their original selection.
        A partial census, changed owner or duplicate ID never proves absence.
        This performs GETs only and never opens a browser or retries a mutation.
        """
        if not artist_id or type(max_pages) is not int or not 1 <= max_pages <= 16:
            raise ValueError("An exact owner and bounded page count are required")
        item = self.project(project_id).get("entities", {}).get("projects", {}).get(project_id)
        if not isinstance(item, dict) or item.get("id") != project_id or item.get("artistId") != artist_id:
            raise ValueError("Project does not match the selected account")
        tracks, cursor, seen, cursors = [], None, set(), set()
        for _ in range(max_pages):
            response = self.project_tracks(project_id, cursor=cursor)
            data = response.get("data", {})
            ids, entities = data.get("trackIds"), response.get("entities", {}).get("tracks", {})
            if not isinstance(ids, list) or len(ids) > 200:
                raise ValueError("Project track page is not bounded")
            for track_id in ids:
                _identifier(track_id)
                track = entities.get(track_id)
                if (track_id in seen or not isinstance(track, dict) or track.get("id") != track_id
                        or track.get("projectId") != project_id or track.get("artistId") != artist_id):
                    raise ValueError("Project track identity changed or repeated")
                seen.add(track_id)
                tracks.append(track)
            following = data.get("nextCursor")
            if following is None:
                return tracks
            if not isinstance(following, str) or not following or following in cursors:
                raise ValueError("Project cursor changed or repeated")
            cursors.add(following)
            cursor = following
        raise ValueError("Project track census exceeded its page bound")

    def create_project(self, title, *, before_dispatch=None):
        request = Request("POST", WEB_ORIGIN + "/api/v1/projects", {"title": title},
                          {"X-Treblo-Platform": "web"})
        if before_dispatch is not None:
            before_dispatch(request)
        return self._execute(request)

    def generate(self, configuration: GenerationV3, project_id, *, access_token,
                 attempt_id, client_version, before_dispatch=None,
                 verification_handler=None, before_verification_dispatch=None):
        if verification_handler is not None and (
                not callable(verification_handler) or not callable(before_verification_dispatch)):
            raise ValueError("Verification needs a normal-widget handler and original dispatch owner")
        request = configuration.prepare(project_id, access_token=access_token,
                                        attempt_id=attempt_id, client_version=client_version)
        if before_dispatch is not None:
            before_dispatch(request)
        prepared, used_kinds = request, set()
        while True:
            try:
                return self._execute(prepared)
            except VerificationRequired as error:
                kind = error.verification_kind
                if verification_handler is None or kind in used_kinds or len(used_kinds) == 3:
                    raise
                used_kinds.add(kind)
                challenge = GenerationChallenge.for_request(
                    request, project_id, attempt_id, kind, len(used_kinds), error.code)
                proof = verification_handler(challenge)
                if proof is None:
                    raise
                if not isinstance(proof, VerificationProof):
                    raise ValueError("The normal verification handler returned no bound proof") from None
                headers, dispatch = proof.consume(challenge)
                prepared = replace(request, headers={**request.headers, **headers})
                # The original owner commits the new physical intent before HTTP.
                # An exception here consumes the proof but sends no continuation.
                before_verification_dispatch(prepared, dispatch)

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
