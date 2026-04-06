import json
import unittest
from unittest import mock

from gpt_register import mail
from gpt_register import oauth as oauth_helpers


class OAuthHelperTests(unittest.TestCase):
    def test_extract_otp_code_matches_expected_patterns(self):
        content = "Subject: Verify\nYour ChatGPT code is 123456"
        self.assertEqual(mail._extract_otp_code(content), "123456")

    def test_submit_callback_url_builds_token_payload(self):
        fake_token_response = {
            "access_token": "access-token",
            "refresh_token": "refresh-token",
            "id_token": "header.payload.signature",
            "expires_in": 3600,
        }
        fake_claims = {
            "email": "user@example.com",
            "https://api.openai.com/auth": {"chatgpt_account_id": "acct_123"},
        }

        with mock.patch.object(oauth_helpers, "_post_form", return_value=fake_token_response), \
             mock.patch.object(oauth_helpers, "_jwt_claims_no_verify", return_value=fake_claims), \
             mock.patch.object(oauth_helpers.time, "time", return_value=1_700_000_000):
            result = oauth_helpers.submit_callback_url(
                callback_url="http://localhost:1455/auth/callback?code=abc&state=xyz",
                expected_state="xyz",
                code_verifier="verifier",
            )

        payload = json.loads(result)
        self.assertEqual(payload["access_token"], "access-token")
        self.assertEqual(payload["refresh_token"], "refresh-token")
        self.assertEqual(payload["account_id"], "acct_123")
        self.assertEqual(payload["email"], "user@example.com")


if __name__ == "__main__":
    unittest.main()
