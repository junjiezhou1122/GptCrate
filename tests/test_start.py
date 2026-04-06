import os
import tempfile
import unittest
from contextlib import contextmanager
from io import StringIO
from contextlib import redirect_stdout
from unittest import mock

import start


@contextmanager
def chdir(path: str):
    previous = os.getcwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(previous)


class StartPyTests(unittest.TestCase):
    def test_select_email_type_defaults_to_imap_on_empty_input(self):
        with mock.patch("builtins.input", return_value=""), redirect_stdout(StringIO()):
            email_type = start.select_email_type()

        self.assertEqual(email_type, "ms_imap")

    def test_get_api_key_reads_quoted_value_from_env(self):
        with tempfile.TemporaryDirectory() as temp_dir, chdir(temp_dir):
            with open(".env", "w", encoding="utf-8") as handle:
                handle.write('LUCKMAIL_API_KEY="secret-key"\n')

            with redirect_stdout(StringIO()):
                api_key = start.get_api_key("luckmail")

            self.assertEqual(api_key, "secret-key")

    def test_generate_env_persists_batch_settings(self):
        with tempfile.TemporaryDirectory() as temp_dir, chdir(temp_dir):
            start.generate_env(
                platform="luckmail",
                api_key="secret-key",
                count=5,
                threads=3,
                luckmail_mode="realtime",
                email_type="ms_graph",
            )

            with open(".env", "r", encoding="utf-8") as handle:
                content = handle.read()

            self.assertIn("BATCH_COUNT=5", content)
            self.assertIn("BATCH_THREADS=3", content)
            self.assertIn("LUCKMAIL_EMAIL_TYPE=ms_graph", content)
            self.assertIn("LUCKMAIL_AUTO_BUY=true", content)
            self.assertIn("LUCKMAIL_SKIP_PURCHASED=false", content)
            self.assertIn("LUCKMAIL_CHECK_WORKERS=20", content)


if __name__ == "__main__":
    unittest.main()
