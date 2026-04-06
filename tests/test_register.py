import unittest

from gpt_register import register


class RegisterFlowTests(unittest.TestCase):
    def test_is_phone_challenge_response_detects_add_phone_page(self):
        payload = {
            "continue_url": "https://auth.openai.com/add-phone",
            "page": {"type": "add_phone"},
        }

        self.assertTrue(register._is_phone_challenge_response(payload))

    def test_is_phone_challenge_response_ignores_normal_continue(self):
        payload = {
            "continue_url": "https://auth.openai.com/about-you",
            "page": {"type": "about_you"},
        }

        self.assertFalse(register._is_phone_challenge_response(payload))


if __name__ == "__main__":
    unittest.main()
