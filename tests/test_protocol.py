import unittest

from treblo_client.protocol import GenerationV3, download_format, password_login, track

PROJECT = "00000000-0000-4000-8000-000000000001"
TRACK = "00000000-0000-4000-8000-000000000002"
ATTEMPT = "00000000-0000-4000-8000-000000000003"


class ProtocolTests(unittest.TestCase):
    def test_custom_vocals_and_exact_duration_survive_preparation(self):
        config = GenerationV3(prompt="Warm guitar", lyrics="Line one\nLine two",
                              instrumental=False, length_range=(120, 210),
                              prompt_strength=1.6, style_scale=2,
                              negative_tags=("distortion",), compare_mode=False)
        request = config.prepare(PROJECT, access_token="test-secret", attempt_id=ATTEMPT,
                                 client_version="observed-build")
        self.assertEqual(request.body["lyrics"], "Line one\nLine two")
        self.assertEqual(request.body["length_range"], [120, 210])
        self.assertIs(request.body["instrumental"], False)
        self.assertNotIn("stream_format", request.body)
        self.assertEqual(request.headers["X-Generation-Attempt-ID"], ATTEMPT)
        self.assertNotIn("test-secret", repr(request))

    def test_conflicting_lyrics_and_nonfinite_duration_are_rejected(self):
        for config in [GenerationV3(lyrics="words", instrumental=True),
                       GenerationV3(length_range=(120, float("inf"))),
                       GenerationV3(length_range=(210, 120))]:
            with self.assertRaises(ValueError):
                config.payload()

    def test_identifiers_cannot_change_the_target_route(self):
        with self.assertRaises(ValueError):
            track("../users/me")

    def test_login_and_download_secrets_are_not_in_request_repr(self):
        request = password_login("account@example.test", "password-secret", "public-key",
                                 captcha_token="captcha-secret")
        self.assertNotIn("password-secret", repr(request))
        self.assertEqual(request.body["gotrue_meta_security"]["captcha_token"], "captcha-secret")
        download = download_format(TRACK, "wav", access_token="token-secret")
        self.assertEqual(download.body, {"track_id": TRACK, "format": "wav"})
        self.assertNotIn("token-secret", repr(download))

    def test_all_observed_download_formats_preserve_the_track(self):
        for format in ["ogg", "wav", "mp3", "flac", "m4a"]:
            request = download_format(TRACK, format, access_token="test-token")
            self.assertEqual(request.body, {"track_id": TRACK, "format": format})
        with self.assertRaises(ValueError):
            download_format(TRACK, "exe", access_token="test-token")


if __name__ == "__main__":
    unittest.main()
