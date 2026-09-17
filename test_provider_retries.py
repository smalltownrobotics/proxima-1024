"""Offline transport retry tests; no credentials, network, or saved-world changes."""
import io
import unittest
import urllib.error
from unittest.mock import call, patch

import providers


# === Bounded provider retries ===
class ProviderRetryTests(unittest.TestCase):
    URL = "https://api.typesafe.ai/v1/systemone"

    def http_error(self, status):
        error = urllib.error.HTTPError(self.URL, status, "Test failure", {}, io.BytesIO())
        self.addCleanup(error.close)
        return error

    def test_jev_overload_retries_same_request_then_returns_provider_response(self):
        response = io.BytesIO(b'{"model":"jev-latest","answers":{}}')
        with patch.object(providers.urllib.request, "urlopen", side_effect=[self.http_error(529), response]) as request, patch.object(providers.time, "sleep") as sleep:
            result = providers.request_json(self.URL, "offline-test-key", {"model": "jev-latest"})
        self.assertEqual(result, {"model": "jev-latest", "answers": {}})
        self.assertEqual(request.call_count, 2)
        self.assertIs(request.call_args_list[0].args[0], request.call_args_list[1].args[0])
        sleep.assert_called_once_with(1)

    def test_jev_overload_stops_after_three_attempts(self):
        with patch.object(providers.urllib.request, "urlopen", side_effect=[self.http_error(529) for _ in range(3)]) as request, patch.object(providers.time, "sleep") as sleep:
            with self.assertRaisesRegex(RuntimeError, "Jev returned HTTP 529. No substitute model was used."):
                providers.request_json(self.URL, "offline-test-key", {})
        self.assertEqual(request.call_count, 3)
        self.assertEqual(sleep.call_args_list, [call(1), call(2)])

    def test_permanent_failure_is_not_retried(self):
        with patch.object(providers.urllib.request, "urlopen", side_effect=self.http_error(401)) as request, patch.object(providers.time, "sleep") as sleep:
            with self.assertRaisesRegex(RuntimeError, "Jev returned HTTP 401. No substitute model was used."):
                providers.request_json(self.URL, "offline-test-key", {})
        request.assert_called_once()
        sleep.assert_not_called()


if __name__ == "__main__":
    unittest.main()
