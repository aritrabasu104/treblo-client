import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from treblo_client.metadata import AudioMetadataError, probe_audio


class MetadataTests(unittest.TestCase):
    def test_header_probe_and_typed_invalid_metadata(self):
        good = {'streams': [{'codec_type': 'audio'}], 'format': {'duration': '1.25', 'format_name': 'wav'}}
        bad = [({'streams': [], 'format': good['format']}, 'invalid_audio'),
               ({'streams': good['streams'], 'format': {'duration': '1', 'format_name': 'mp3'}}, 'download_format_mismatch'),
               ({'streams': good['streams'], 'format': {'duration': 'NaN', 'format_name': 'wav'}}, 'invalid_duration')]
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / 'track.wav'; path.write_bytes(b'synthetic-header')
            with patch('treblo_client.metadata.subprocess.run', return_value=Mock(stdout=json.dumps(good))) as run:
                self.assertEqual(probe_audio(path, format='wav'), good)
                self.assertIn('-analyzeduration', run.call_args.args[0])
                self.assertNotIn('ffmpeg', run.call_args.args[0])
            for response, code in bad:
                with self.subTest(code=code), patch('treblo_client.metadata.subprocess.run', return_value=Mock(stdout=json.dumps(response))):
                    with self.assertRaises(AudioMetadataError) as caught: probe_audio(path, format='wav')
                    self.assertEqual(caught.exception.code, code)
