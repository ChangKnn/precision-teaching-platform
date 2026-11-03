"""Retry a transient TLS setup failure without bypassing certificate checks."""
import io
import ssl
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError

from backend.app.services import ai


class ModelTLSRecoveryTest(unittest.TestCase):
    def send(self):
        return ai._post_json('https://example.test/chat/completions', {}, 'test-key')

    def test_tls_setup_eof_retries_once(self):
        error = URLError(ssl.SSLEOFError(8, 'UNEXPECTED_EOF_WHILE_READING'))
        with patch.object(ai, 'record_llm_request'), patch.object(ai.time, 'sleep'), patch.object(ai, 'urlopen', side_effect=[error, io.BytesIO(b'{"ok":true}')]) as post:
            self.assertEqual(self.send(), {'ok': True})
            self.assertEqual(post.call_count, 2)
            for call in post.call_args_list:
                context = call.kwargs['context']
                self.assertTrue(context.check_hostname)
                self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)
                self.assertLessEqual(call.kwargs['timeout'], 120)

    def test_repeated_eof_stops(self):
        with patch.object(ai, 'record_llm_request'), patch.object(ai.time, 'sleep'), patch.object(ai, 'urlopen', side_effect=URLError(ssl.SSLEOFError(8, 'EOF'))) as post:
            with self.assertRaisesRegex(RuntimeError, '重连后仍未成功'):
                self.send()
            self.assertEqual(post.call_count, 2)

    def test_certificate_error_is_never_retried(self):
        with patch.object(ai, 'record_llm_request'), patch.object(ai, 'urlopen', side_effect=URLError(ssl.SSLCertVerificationError(1, 'certificate verify failed'))) as post:
            with self.assertRaisesRegex(RuntimeError, 'certificate verify failed'):
                self.send()
            self.assertEqual(post.call_count, 1)

    def test_authentication_error_is_never_retried(self):
        with patch.object(ai, 'record_llm_request'), patch.object(ai, 'urlopen', side_effect=HTTPError('https://example.test', 401, 'Unauthorized', {}, io.BytesIO(b'bad key'))) as post:
            with self.assertRaisesRegex(RuntimeError, '401'):
                self.send()
            self.assertEqual(post.call_count, 1)

    def test_started_response_is_not_retried(self):
        class BrokenResponse(io.BytesIO):
            def read(self, *args):
                raise ssl.SSLEOFError(8, 'EOF')
        with patch.object(ai, 'record_llm_request'), patch.object(ai, 'urlopen', return_value=BrokenResponse()) as post:
            with self.assertRaisesRegex(RuntimeError, '回复传输中断'):
                self.send()
            self.assertEqual(post.call_count, 1)
