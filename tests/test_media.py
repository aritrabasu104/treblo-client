from email.message import Message
import hashlib
import io
from pathlib import Path
import tempfile
import unittest
import wave

from treblo_client import stream_download


class Response(io.BytesIO):
    def __init__(self, body, length=None):
        super().__init__(body); self.code = 200; self.headers = Message()
        self.headers['Content-Length'] = str(len(body) if length is None else length)


class Opener:
    def __init__(self, body, length=None): self.body, self.length, self.requests = body, length, []
    def open(self, request, **kwargs): self.requests.append(request); return Response(self.body, self.length)


class MediaTests(unittest.TestCase):
    def wav(self):
        data = io.BytesIO()
        with wave.open(data, 'wb') as audio:
            audio.setnchannels(1); audio.setsampwidth(2); audio.setframerate(8000)
            audio.writeframes(b'\0\0' * 8000)
        return data.getvalue()

    def test_transfer_hash_duration_exclusive_destination_and_no_forwarded_credentials(self):
        with tempfile.TemporaryDirectory() as root:
            body = self.wav(); opener = Opener(body); path = Path(root).resolve() / 'track.wav'
            result = stream_download('https://cdn.treblo.com/synthetic.wav?signature=synthetic', path, format='wav', opener=opener)
            self.assertEqual(result.sha256, hashlib.sha256(body).hexdigest())
            self.assertEqual(result.duration_seconds, 1)
            self.assertEqual(path.read_bytes(), body)
            self.assertFalse(path.with_name('track.wav.part').exists())
            self.assertFalse(any(k.lower() in {'cookie', 'authorization'} for k in opener.requests[0].headers))
            with self.assertRaises(FileExistsError): stream_download('https://cdn.treblo.com/synthetic.wav', path, format='wav', opener=opener)
            self.assertEqual(len(opener.requests), 1)

    def test_truncation_wrong_container_and_size_limit_never_publish_or_leave_own_partial(self):
        for body, length, limit in [(self.wav(), 999999, 1000000), (b'<html>error</html>', None, 1000000), (self.wav(), None, 10)]:
            with self.subTest(length=length, limit=limit), tempfile.TemporaryDirectory() as root:
                path = Path(root).resolve() / 'track.wav'
                with self.assertRaises(ValueError):
                    stream_download('https://cdn.treblo.com/synthetic.wav', path, format='wav', opener=Opener(body, length), max_bytes=limit)
                self.assertFalse(path.exists()); self.assertFalse(path.with_name('track.wav.part').exists())

    def test_external_or_indirect_destinations_never_dispatch(self):
        with tempfile.TemporaryDirectory() as root:
            opener = Opener(self.wav()); path = Path(root).resolve() / 'track.wav'
            for url in ['http://cdn.treblo.com/a', 'https://other.example/a', 'https://cdn.treblo.com@other.example/a']:
                with self.assertRaises(ValueError): stream_download(url, path, format='wav', opener=opener)
            path.symlink_to(Path(root).resolve() / 'other')
            with self.assertRaises(ValueError): stream_download('https://cdn.treblo.com/a', path, format='wav', opener=opener)
            self.assertEqual(opener.requests, [])
