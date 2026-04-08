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

    def test_get_api_key_skips_prompt_for_local_outlook(self):
        with redirect_stdout(StringIO()):
            api_key = start.get_api_key("local_outlook")

        self.assertEqual(api_key, "")

    def test_get_api_key_skips_prompt_for_cf(self):
        with redirect_stdout(StringIO()):
            api_key = start.get_api_key("cf")

        self.assertEqual(api_key, "")

    def test_get_cf_config_reads_existing_env_values(self):
        with tempfile.TemporaryDirectory() as temp_dir, chdir(temp_dir):
            with open(".env", "w", encoding="utf-8") as handle:
                handle.write("MAIL_DOMAIN=example.com\n")
                handle.write("MAIL_WORKER_BASE=https://worker.example.com\n")
                handle.write("MAIL_ADMIN_PASSWORD=secret\n")

            with mock.patch("builtins.input", side_effect=["", "", ""]), redirect_stdout(StringIO()):
                domain, worker_base, password = start.get_cf_config()

            self.assertEqual(domain, "example.com")
            self.assertEqual(worker_base, "https://worker.example.com")
            self.assertEqual(password, "secret")

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

    def test_generate_env_supports_luckmail_own_mode(self):
        with tempfile.TemporaryDirectory() as temp_dir, chdir(temp_dir):
            start.generate_env(
                platform="luckmail",
                api_key="secret-key",
                count=2,
                threads=1,
                luckmail_mode="own",
            )

            with open(".env", "r", encoding="utf-8") as handle:
                content = handle.read()

            self.assertIn("LUCKMAIL_AUTO_BUY=true", content)
            self.assertIn("LUCKMAIL_SKIP_PURCHASED=true", content)
            self.assertIn("LUCKMAIL_PURCHASED_ONLY=false", content)
            self.assertIn("LUCKMAIL_OWN_ONLY=true", content)

    def test_generate_env_supports_local_outlook_mode(self):
        with tempfile.TemporaryDirectory() as temp_dir, chdir(temp_dir):
            start.generate_env(
                platform="local_outlook",
                api_key="",
                count=2,
                threads=1,
                local_outlook_mail_mode="imap",
            )

            with open(".env", "r", encoding="utf-8") as handle:
                content = handle.read()

            self.assertIn("EMAIL_MODE=local_outlook", content)
            self.assertIn("ACCOUNTS_FILE=accounts.txt", content)
            self.assertIn("邮箱----密码----client_id----refresh_token", content)
            self.assertIn("LOCAL_OUTLOOK_MAIL_MODE=imap", content)
            self.assertIn("LOCAL_OUTLOOK_BAD_FILE=bad_local_outlook.txt", content)

    def test_generate_env_supports_cf_mode(self):
        with tempfile.TemporaryDirectory() as temp_dir, chdir(temp_dir):
            start.generate_env(
                platform="cf",
                api_key="",
                count=1,
                threads=1,
                cf_domain="example.com",
                cf_worker_base="https://worker.example.com",
                cf_admin_password="secret",
            )

            with open(".env", "r", encoding="utf-8") as handle:
                content = handle.read()

            self.assertIn("EMAIL_MODE=cf", content)
            self.assertIn("MAIL_DOMAIN=example.com", content)
            self.assertIn("MAIL_WORKER_BASE=https://worker.example.com", content)
            self.assertIn("MAIL_ADMIN_PASSWORD=secret", content)


if __name__ == "__main__":
    unittest.main()
