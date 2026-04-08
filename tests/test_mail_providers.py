import unittest
from unittest import mock

from gpt_register import context as ctx
from gpt_register import hotmail
from gpt_register import mail
from gpt_register import luckmail


class MailProviderTests(unittest.TestCase):
    def setUp(self):
        self._original = {
            "EMAIL_MODE": ctx.EMAIL_MODE,
            "MAIL_DOMAIN": ctx.MAIL_DOMAIN,
            "LUCKMAIL_AUTO_BUY": ctx.LUCKMAIL_AUTO_BUY,
            "LUCKMAIL_OWN_ONLY": ctx.LUCKMAIL_OWN_ONLY,
            "LOCAL_OUTLOOK_MAIL_MODE": ctx.LOCAL_OUTLOOK_MAIL_MODE,
            "LOCAL_OUTLOOK_BAD_FILE": ctx.LOCAL_OUTLOOK_BAD_FILE,
            "LUCKMAIL_API_KEY": ctx.LUCKMAIL_API_KEY,
            "HOTMAIL007_API_KEY": ctx.HOTMAIL007_API_KEY,
            "_email_queue": ctx._email_queue,
            "_active_email_queue": ctx._active_email_queue,
            "_luckmail_purchased_only": ctx._luckmail_purchased_only,
            "_luckmail_own_only": ctx._luckmail_own_only,
            "_hotmail007_credentials": dict(ctx._hotmail007_credentials),
            "_luckmail_credentials": dict(ctx._luckmail_credentials),
        }
        ctx._hotmail007_credentials.clear()
        ctx._luckmail_credentials.clear()

    def tearDown(self):
        ctx.EMAIL_MODE = self._original["EMAIL_MODE"]
        ctx.MAIL_DOMAIN = self._original["MAIL_DOMAIN"]
        ctx.LUCKMAIL_AUTO_BUY = self._original["LUCKMAIL_AUTO_BUY"]
        ctx.LUCKMAIL_OWN_ONLY = self._original["LUCKMAIL_OWN_ONLY"]
        ctx.LOCAL_OUTLOOK_MAIL_MODE = self._original["LOCAL_OUTLOOK_MAIL_MODE"]
        ctx.LOCAL_OUTLOOK_BAD_FILE = self._original["LOCAL_OUTLOOK_BAD_FILE"]
        ctx.LUCKMAIL_API_KEY = self._original["LUCKMAIL_API_KEY"]
        ctx.HOTMAIL007_API_KEY = self._original["HOTMAIL007_API_KEY"]
        ctx._email_queue = self._original["_email_queue"]
        ctx._active_email_queue = self._original["_active_email_queue"]
        ctx._luckmail_purchased_only = self._original["_luckmail_purchased_only"]
        ctx._luckmail_own_only = self._original["_luckmail_own_only"]
        ctx._hotmail007_credentials.clear()
        ctx._hotmail007_credentials.update(self._original["_hotmail007_credentials"])
        ctx._luckmail_credentials.clear()
        ctx._luckmail_credentials.update(self._original["_luckmail_credentials"])

    def test_get_email_and_token_dispatches_to_cloudflare_mode(self):
        ctx.EMAIL_MODE = "cf"
        ctx.MAIL_DOMAIN = "example.com"

        email, token = mail.get_email_and_token()

        self.assertEqual(email, token)
        self.assertTrue(email.endswith("@example.com"))

    def test_get_email_and_token_dispatches_to_hotmail007_mode(self):
        ctx.EMAIL_MODE = "hotmail007"
        ctx.HOTMAIL007_API_KEY = "key"
        fake_mail = {
            "email": "user@example.com",
            "password": "secret",
            "refresh_token": "refresh",
            "client_id": "client",
        }

        with mock.patch.object(hotmail, "hotmail007_get_mail", return_value=([fake_mail], "")), \
             mock.patch.object(hotmail, "_outlook_get_known_ids", return_value={"known-id"}):
            email, token = mail.get_email_and_token()

        self.assertEqual((email, token), ("user@example.com", "user@example.com"))
        self.assertEqual(ctx._hotmail007_credentials["user@example.com"]["known_ids"], {"known-id"})

    def test_get_email_and_token_dispatches_to_local_outlook_mode(self):
        ctx.EMAIL_MODE = "local_outlook"
        ctx.LOCAL_OUTLOOK_MAIL_MODE = "graph"

        class FakeQueue:
            def __init__(self):
                self.used = False

            def __len__(self):
                return 0 if self.used else 1

            def pop(self):
                if self.used:
                    return None
                self.used = True
                return {
                    "email": "local@example.com",
                    "password": "ms-pass",
                    "client_id": "client-id",
                    "refresh_token": "refresh-token",
                }

        ctx._email_queue = FakeQueue()

        with mock.patch.object(hotmail, "_outlook_get_graph_token", return_value="access-token"), \
             mock.patch.object(hotmail, "_outlook_get_known_ids", return_value={"known-id"}):
            email, token = mail.get_email_and_token()

        self.assertEqual((email, token), ("local@example.com", "local@example.com"))
        self.assertEqual(ctx._hotmail007_credentials["local@example.com"]["client_id"], "client-id")
        self.assertEqual(ctx._hotmail007_credentials["local@example.com"]["known_ids"], {"known-id"})

    def test_local_outlook_invalid_account_is_recorded_and_skipped(self):
        import os
        import tempfile

        ctx.EMAIL_MODE = "local_outlook"
        ctx.LOCAL_OUTLOOK_MAIL_MODE = "graph"

        with tempfile.TemporaryDirectory() as temp_dir:
            bad_file = os.path.join(temp_dir, "bad.txt")
            ctx.LOCAL_OUTLOOK_BAD_FILE = bad_file

            class FakeQueue:
                def __init__(self):
                    self.items = [
                        {
                            "email": "bad@example.com",
                            "password": "bad-pass",
                            "client_id": "bad-client",
                            "refresh_token": "bad-refresh",
                        },
                        {
                            "email": "good@example.com",
                            "password": "good-pass",
                            "client_id": "good-client",
                            "refresh_token": "good-refresh",
                        },
                    ]

                def __len__(self):
                    return len(self.items)

                def pop(self):
                    return self.items.pop(0) if self.items else None

            ctx._email_queue = FakeQueue()

            def fake_graph_token(client_id, refresh_token, proxies=None):
                if client_id == "bad-client":
                    raise Exception("invalid_grant")
                return "access-token"

            with mock.patch.object(hotmail, "_outlook_get_graph_token", side_effect=fake_graph_token), \
                 mock.patch.object(hotmail, "_outlook_get_known_ids", return_value=set()):
                email, token = mail.get_email_and_token()

            self.assertEqual((email, token), ("good@example.com", "good@example.com"))
            self.assertTrue(os.path.exists(bad_file))
            with open(bad_file, "r", encoding="utf-8") as handle:
                bad_content = handle.read()
            self.assertIn("bad@example.com----bad-pass----bad-client----bad-refresh", bad_content)

    def test_local_outlook_uses_imap_mode_when_configured(self):
        ctx.EMAIL_MODE = "local_outlook"
        ctx.LOCAL_OUTLOOK_MAIL_MODE = "imap"

        class FakeQueue:
            def __len__(self):
                return 0

            def pop(self):
                return {
                    "email": "imap@example.com",
                    "password": "imap-pass",
                    "client_id": "imap-client",
                    "refresh_token": "imap-refresh",
                }

        ctx._email_queue = FakeQueue()

        with mock.patch.object(hotmail, "_outlook_get_imap_token", return_value=("token", "outlook.office365.com")) as imap_mock, \
             mock.patch.object(hotmail, "_outlook_get_known_ids", return_value=set()):
            email, token = mail.get_email_and_token()

        self.assertEqual((email, token), ("imap@example.com", "imap@example.com"))
        self.assertEqual(ctx._hotmail007_credentials["imap@example.com"]["mail_mode"], "imap")
        imap_mock.assert_called()

    def test_local_outlook_mail_error_records_bad_account(self):
        import os
        import tempfile

        with tempfile.TemporaryDirectory() as temp_dir:
            bad_file = os.path.join(temp_dir, "bad.txt")
            ctx.LOCAL_OUTLOOK_BAD_FILE = bad_file
            ctx._hotmail007_credentials["broken@example.com"] = {
                "client_id": "client",
                "refresh_token": "refresh",
                "mail_mode": "graph",
                "source": "local_outlook",
                "account_line": "broken@example.com----pass----client----refresh",
                "known_ids": set(),
            }

            with mock.patch.object(hotmail, "_outlook_fetch_otp", return_value="") as fetch_mock:
                def inject_error(*args, **kwargs):
                    ctx._hotmail007_credentials["broken@example.com"]["last_mail_error"] = "token_error:invalid_grant"
                    return ""

                fetch_mock.side_effect = inject_error
                code = hotmail.get_oai_code("broken@example.com")

            self.assertEqual(code, "")
            self.assertTrue(os.path.exists(bad_file))
            with open(bad_file, "r", encoding="utf-8") as handle:
                self.assertIn("broken@example.com----pass----client----refresh", handle.read())

    def test_get_email_and_token_dispatches_to_luckmail_order_mode(self):
        ctx.EMAIL_MODE = "luckmail"
        ctx.LUCKMAIL_API_KEY = "key"
        ctx.LUCKMAIL_AUTO_BUY = False

        with mock.patch.object(
            luckmail,
            "luckmail_create_order",
            return_value=("order-1", {"email_address": "luck@example.com"}),
        ):
            email, token = mail.get_email_and_token()

        self.assertEqual((email, token), ("luck@example.com", "luck@example.com"))
        self.assertEqual(ctx._luckmail_credentials["luck@example.com"]["order_no"], "order-1")

    def test_select_latest_unseen_code_prefers_fresh_mail(self):
        mails = [
            {
                "message_id": "old-1",
                "received_at": "2026-04-06 04:57:38",
                "subject": "Your ChatGPT code is 879511",
            },
            {
                "message_id": "new-1",
                "received_at": "2026-04-06 08:17:22",
                "subject": "Your ChatGPT code is 721049",
            },
        ]

        code, message_id = luckmail._select_latest_unseen_code(mails, {"old-1"})

        self.assertEqual(code, "721049")
        self.assertEqual(message_id, "new-1")

    def test_get_oai_code_prefers_mail_list_before_token_endpoint(self):
        ctx._luckmail_credentials["gmail@example.com"] = {
            "token": "tok_test",
            "known_message_ids": {"old-1"},
        }
        seen_ids = set()
        mails = [
            {
                "message_id": "old-1",
                "received_at": "2026-04-06 04:57:38",
                "subject": "Your ChatGPT code is 879511",
            },
            {
                "message_id": "new-1",
                "received_at": "2026-04-06 08:17:22",
                "subject": "Your ChatGPT code is 721049",
            },
        ]

        with mock.patch.object(luckmail, "luckmail_get_token_mails", return_value=(mails, None)), \
             mock.patch.object(luckmail, "luckmail_get_code_by_token", return_value="879511") as fallback_mock:
            code = luckmail.get_oai_code("gmail@example.com", seen_ids=seen_ids)

        self.assertEqual(code, "721049")
        self.assertEqual(seen_ids, {"new-1"})
        fallback_mock.assert_not_called()

    def test_get_oai_code_uses_existing_order_before_creating_new_one(self):
        ctx._luckmail_credentials["order@example.com"] = {"order_no": "order-1"}

        with mock.patch.object(luckmail, "luckmail_get_code", return_value="654321") as code_mock, \
             mock.patch.object(luckmail, "luckmail_create_order") as create_order_mock:
            code = luckmail.get_oai_code("order@example.com")

        self.assertEqual(code, "654321")
        code_mock.assert_called_once_with("order-1", proxies=None)
        create_order_mock.assert_not_called()

    def test_collect_private_emails_pushes_normal_emails_into_queue(self):
        private_emails = [
            {"id": 1, "address": "user1@outlook.com", "status": 1, "type": "ms_graph"},
            {"id": 2, "address": "user2@hotmail.com", "status": 1, "type": "ms_imap"},
        ]
        active_queue = ctx.ActiveEmailQueue()

        with mock.patch.object(luckmail, "luckmail_get_all_private_emails", return_value=(private_emails, None)):
            active = luckmail.luckmail_collect_private_emails(active_queue=active_queue)

        self.assertEqual(
            active,
            [
                {"email": "user1@outlook.com", "id": 1, "source": "private", "type": "ms_graph"},
                {"email": "user2@hotmail.com", "id": 2, "source": "private", "type": "ms_imap"},
            ],
        )
        self.assertEqual(len(active_queue), 2)

    def test_get_email_and_token_uses_private_email_queue_in_own_mode(self):
        ctx.EMAIL_MODE = "luckmail"
        ctx.LUCKMAIL_API_KEY = "key"
        ctx.LUCKMAIL_AUTO_BUY = True
        ctx.LUCKMAIL_OWN_ONLY = True
        ctx._luckmail_own_only = True
        ctx._active_email_queue = ctx.ActiveEmailQueue()
        ctx._active_email_queue.add_batch([{"email": "user@outlook.com", "id": 9, "source": "private"}])

        with mock.patch.object(luckmail, "luckmail_get_private_email_mails", return_value=([], None, 0)):
            email, token = mail.get_email_and_token()

        self.assertEqual((email, token), ("user@outlook.com", "user@outlook.com"))
        self.assertEqual(ctx._luckmail_credentials["user@outlook.com"]["email_id"], 9)
        self.assertEqual(ctx._luckmail_credentials["user@outlook.com"]["source"], "private")

    def test_get_oai_code_reads_private_email_mails(self):
        ctx._luckmail_credentials["own@example.com"] = {
            "email_id": 12,
            "known_message_ids": {"old-1"},
        }
        seen_ids = set()
        mails = [
            {
                "message_id": "new-1",
                "received_at": "2026-04-06 08:17:22",
                "subject": "Your ChatGPT code is 721049",
            },
        ]

        with mock.patch.object(luckmail, "luckmail_get_private_email_mails", return_value=(mails, None, 1)), \
             mock.patch.object(luckmail, "luckmail_get_private_email_mail_detail") as detail_mock:
            code = luckmail.get_oai_code("own@example.com", seen_ids=seen_ids)

        self.assertEqual(code, "721049")
        self.assertEqual(seen_ids, {"new-1"})
        detail_mock.assert_not_called()

    def test_check_purchased_emails_filters_non_hotmail_and_streams_active_results(self):
        purchased = [
            {"email_address": "user1@hotmail.com", "token": "tok-1", "id": 1},
            {"email_address": "user2@outlook.com", "token": "tok-2", "id": 2},
            {"email_address": "user3@hotmail.com", "token": "tok-3", "id": 3},
        ]
        active_queue = ctx.ActiveEmailQueue()

        def fake_alive(token: str, proxies=None):
            return (token == "tok-3", "ok")

        with mock.patch.object(luckmail, "luckmail_get_all_purchased_emails", return_value=(purchased, None)), \
             mock.patch.object(luckmail, "luckmail_check_email_alive", side_effect=fake_alive) as alive_mock:
            active = luckmail.luckmail_check_purchased_emails(max_workers=2, active_queue=active_queue)

        self.assertEqual(active, [{"email": "user3@hotmail.com", "token": "tok-3", "id": 3}])
        self.assertEqual(len(active_queue), 1)
        self.assertEqual(active_queue.pop(), {"email": "user3@hotmail.com", "token": "tok-3", "id": 3})
        self.assertEqual(alive_mock.call_count, 2)


if __name__ == "__main__":
    unittest.main()
