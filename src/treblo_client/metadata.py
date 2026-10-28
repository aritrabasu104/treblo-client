"""Header-only local metadata probe; no full-stream decode or provider calls."""
import json
import math
from pathlib import Path
import subprocess


class AudioMetadataError(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def probe_audio(path, *, format):
    """Verify an audio container and duration using an installed FFprobe.

    This bounded metadata probe is separate from full decoding or subjective
    quality assessment. Callers decide whether those additional checks apply.
    """
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        raise ValueError("Audio probe requires a regular file")
    try:
        result = subprocess.run(['ffprobe', '-v', 'error', '-protocol_whitelist', 'file,pipe',
            '-probesize', '1048576', '-analyzeduration', '0', '-show_entries',
            'format=duration,format_name:stream=codec_type,codec_name', '-of', 'json', str(path)],
            capture_output=True, text=True, check=True, timeout=30)
        if len(result.stdout) > 1024 * 1024:
            raise ValueError("Audio metadata exceeded its output limit")
        metadata = json.loads(result.stdout)
    except (OSError, subprocess.SubprocessError, KeyError, TypeError, ValueError):
        raise AudioMetadataError('audio_metadata_unverified') from None
    if not any(s.get('codec_type') == 'audio' for s in metadata.get('streams', [])):
        raise AudioMetadataError('invalid_audio')
    expected = {'m4a': {'mov', 'mp4', 'm4a'}, 'mp3': {'mp3'},
                'wav': {'wav'}, 'flac': {'flac'}, 'ogg': {'ogg'}}[format]
    actual = set(metadata.get('format', {}).get('format_name', '').split(','))
    if not actual.intersection(expected):
        raise AudioMetadataError('download_format_mismatch')
    try:
        duration = float(metadata['format']['duration'])
    except (KeyError, ValueError, TypeError):
        raise AudioMetadataError('invalid_duration') from None
    if not math.isfinite(duration) or duration <= 0:
        raise AudioMetadataError('invalid_duration')
    return metadata
