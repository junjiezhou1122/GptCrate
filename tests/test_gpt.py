import os
import sys
import tempfile
import unittest
from contextlib import ExitStack, redirect_stdout
from io import StringIO
from unittest import mock

from gpt_register import cli
from gpt_register import context as ctx


class FakeThread:
    instances = []

    def __init__(self, target=None, args=(), kwargs=None, daemon=None):
        self.target = target
        self.args = args
        self.kwargs = kwargs or {}
        self.daemon = daemon
        self.started = False
        FakeThread.instances.append(self)

    def start(self):
        self.started = True

    def join(self, timeout=None):
        return None

    def is_alive(self):
        return False


class GptMainTests(unittest.TestCase):
    def setUp(self):
        FakeThread.instances = []
        self._original_globals = {
            "EMAIL_MODE": ctx.EMAIL_MODE,
            "ACCOUNTS_FILE": ctx.ACCOUNTS_FILE,
            "LUCKMAIL_AUTO_BUY": ctx.LUCKMAIL_AUTO_BUY,
            "LUCKMAIL_PURCHASED_ONLY": ctx.LUCKMAIL_PURCHASED_ONLY,
            "LUCKMAIL_CHECK_WORKERS": ctx.LUCKMAIL_CHECK_WORKERS,
            "LUCKMAIL_MAX_RETRY": ctx.LUCKMAIL_MAX_RETRY,
            "_email_queue": ctx._email_queue,
            "_active_email_queue": ctx._active_email_queue,
            "_success_counter": ctx._success_counter,
        }

    def tearDown(self):
        for key, value in self._original_globals.items():
            setattr(ctx, key, value)

    def test_main_applies_cli_overrides_and_starts_stats_thread_once(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            accounts_file = os.path.join(temp_dir, "accounts.txt")
            with open(accounts_file, "w", encoding="utf-8") as handle:
                handle.write("user@example.com\n")

            argv = [
                "gpt.py",
                "--email-mode",
                "file",
                "--accounts-file",
                accounts_file,
                "--count",
                "1",
                "--luckmail-max-retry",
                "7",
            ]

            with ExitStack() as stack:
                worker_mock = stack.enter_context(mock.patch.object(cli, "_worker"))
                stack.enter_context(mock.patch.object(ctx, "_load_proxies", return_value=[]))
                stack.enter_context(mock.patch.object(cli.threading, "Thread", FakeThread))
                stack.enter_context(mock.patch.object(sys, "argv", argv))
                with redirect_stdout(StringIO()):
                    cli.main()

            self.assertEqual(ctx.ACCOUNTS_FILE, accounts_file)
            self.assertEqual(ctx.LUCKMAIL_MAX_RETRY, 7)
            self.assertEqual(len(ctx._email_queue), 1)
            self.assertEqual(len(FakeThread.instances), 1)
            worker_mock.assert_called_once()

    def test_main_uses_once_flag_as_single_batch_run(self):
        ctx.EMAIL_MODE = "cf"
        ctx.LUCKMAIL_AUTO_BUY = False
        argv = ["gpt.py", "--once"]

        with ExitStack() as stack:
            worker_mock = stack.enter_context(mock.patch.object(cli, "_worker"))
            stack.enter_context(mock.patch.object(ctx, "_load_proxies", return_value=[]))
            stack.enter_context(mock.patch.object(cli.threading, "Thread", FakeThread))
            stack.enter_context(mock.patch.object(cli.time, "sleep", return_value=None))
            stack.enter_context(mock.patch.object(sys, "argv", argv))
            with redirect_stdout(StringIO()):
                cli.main()

        self.assertEqual(len(FakeThread.instances), 1)
        worker_mock.assert_called_once()
        self.assertEqual(worker_mock.call_args.kwargs["count_target"], 1)
        self.assertEqual(worker_mock.call_args.kwargs["remaining"], [1])

    def test_main_uses_env_batch_threads_when_cli_keeps_default_thread_count(self):
        ctx.EMAIL_MODE = "cf"
        ctx.LUCKMAIL_AUTO_BUY = False
        original_batch_threads = ctx.BATCH_THREADS
        ctx.BATCH_THREADS = "4"
        argv = ["gpt.py", "--count", "3"]

        try:
            with ExitStack() as stack:
                worker_mock = stack.enter_context(mock.patch.object(cli, "_worker"))
                stack.enter_context(mock.patch.object(ctx, "_load_proxies", return_value=[]))
                stack.enter_context(mock.patch.object(cli.threading, "Thread", FakeThread))
                stack.enter_context(mock.patch.object(cli.time, "sleep", return_value=None))
                stack.enter_context(mock.patch.object(sys, "argv", argv))
                with redirect_stdout(StringIO()):
                    cli.main()
        finally:
            ctx.BATCH_THREADS = original_batch_threads

        self.assertEqual(worker_mock.call_count, 0)
        self.assertEqual(len(FakeThread.instances), 4)
        worker_threads = [thread for thread in FakeThread.instances if thread.target is worker_mock]
        self.assertEqual(len(worker_threads), 3)
        self.assertTrue(all(thread.started for thread in worker_threads))

    def test_main_stops_early_when_purchased_only_finds_no_active_hotmail(self):
        ctx.EMAIL_MODE = "luckmail"
        ctx.LUCKMAIL_AUTO_BUY = True
        original_purchased_only = ctx.LUCKMAIL_PURCHASED_ONLY
        original_check_workers = ctx.LUCKMAIL_CHECK_WORKERS
        ctx.LUCKMAIL_PURCHASED_ONLY = True
        ctx.LUCKMAIL_CHECK_WORKERS = 8
        argv = ["gpt.py"]

        try:
            with ExitStack() as stack:
                worker_mock = stack.enter_context(mock.patch.object(cli, "_worker"))
                prefetch_mock = stack.enter_context(mock.patch.object(cli.mail, "_prefetch_active_emails"))
                stack.enter_context(mock.patch.object(ctx, "_load_proxies", return_value=[]))
                stack.enter_context(mock.patch.object(cli.threading, "Thread", FakeThread))
                stack.enter_context(mock.patch.object(sys, "argv", argv))
                with redirect_stdout(StringIO()):
                    cli.main()
        finally:
            ctx.LUCKMAIL_PURCHASED_ONLY = original_purchased_only
            ctx.LUCKMAIL_CHECK_WORKERS = original_check_workers

        worker_mock.assert_not_called()
        self.assertEqual(len(FakeThread.instances), 1)
        self.assertIs(FakeThread.instances[0].target, prefetch_mock)
        self.assertEqual(FakeThread.instances[0].args[1:], (10, 20))


if __name__ == "__main__":
    unittest.main()
