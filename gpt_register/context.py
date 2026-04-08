import os
import threading
import time
from typing import Dict, List, Optional



def _load_dotenv(path: str = ".env") -> None:
    if not os.path.exists(path):
        return
    try:
        with open(path, "r", encoding="utf-8") as handle:
            for raw in handle:
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                key = key.strip()
                if not key or key in os.environ:
                    continue
                value = value.strip()
                if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
                    value = value[1:-1]
                os.environ[key] = value
    except Exception:
        pass


_load_dotenv()

MAIL_DOMAIN = os.getenv("MAIL_DOMAIN", "")
MAIL_WORKER_BASE = os.getenv("MAIL_WORKER_BASE", "").rstrip("/")
MAIL_ADMIN_PASSWORD = os.getenv("MAIL_ADMIN_PASSWORD", "")
TOKEN_OUTPUT_DIR = (os.getenv("TOKEN_OUTPUT_DIR") or "./tokens").strip()
CLI_PROXY_AUTHS_DIR = os.getenv("CLI_PROXY_AUTHS_DIR", "").strip()

PROXY_FILE = os.getenv("PROXY_FILE", "").strip()
SINGLE_PROXY = os.getenv("PROXY", "").strip()
BATCH_COUNT = os.getenv("BATCH_COUNT", "").strip()
BATCH_THREADS = os.getenv("BATCH_THREADS", "").strip()

EMAIL_MODE = os.getenv("EMAIL_MODE", "cf").strip().lower()
HOTMAIL007_API_URL = os.getenv("HOTMAIL007_API_URL", "https://gapi.hotmail007.com").rstrip("/")
HOTMAIL007_API_KEY = os.getenv("HOTMAIL007_API_KEY", "").strip()
HOTMAIL007_MAIL_TYPE = os.getenv("HOTMAIL007_MAIL_TYPE", "outlook Trusted Graph").strip()
HOTMAIL007_MAIL_MODE = os.getenv("HOTMAIL007_MAIL_MODE", "graph").strip().lower()
LOCAL_OUTLOOK_MAIL_MODE = os.getenv("LOCAL_OUTLOOK_MAIL_MODE", "graph").strip().lower()
LOCAL_OUTLOOK_BAD_FILE = os.getenv("LOCAL_OUTLOOK_BAD_FILE", "bad_local_outlook.txt").strip()

LUCKMAIL_API_KEY = os.getenv("LUCKMAIL_API_KEY", "").strip()
LUCKMAIL_API_URL = os.getenv("LUCKMAIL_API_URL", "https://mails.luckyous.com/api/v1/openapi").rstrip("/")
LUCKMAIL_AUTO_BUY = os.getenv("LUCKMAIL_AUTO_BUY", "true").strip().lower() == "true"
LUCKMAIL_PURCHASED_ONLY = os.getenv("LUCKMAIL_PURCHASED_ONLY", "false").strip().lower() == "true"
LUCKMAIL_SKIP_PURCHASED = os.getenv("LUCKMAIL_SKIP_PURCHASED", "false").strip().lower() == "true"
LUCKMAIL_OWN_ONLY = os.getenv("LUCKMAIL_OWN_ONLY", "false").strip().lower() == "true"
LUCKMAIL_MAIL_DEBUG = os.getenv("LUCKMAIL_MAIL_DEBUG", "false").strip().lower() == "true"
LUCKMAIL_EMAIL_TYPE = os.getenv("LUCKMAIL_EMAIL_TYPE", "ms_imap").strip().lower()
try:
    LUCKMAIL_MAX_RETRY = int(os.getenv("LUCKMAIL_MAX_RETRY", "3").strip())
except ValueError:
    LUCKMAIL_MAX_RETRY = 3
try:
    LUCKMAIL_CHECK_WORKERS = max(1, int(os.getenv("LUCKMAIL_CHECK_WORKERS", "20").strip()))
except ValueError:
    LUCKMAIL_CHECK_WORKERS = 20

ACCOUNTS_FILE = os.getenv("ACCOUNTS_FILE", "accounts.txt").strip()
AUTO_REGISTER_THRESHOLD = 10



def _load_proxies(filepath: str) -> List[str]:
    proxies_list: List[str] = []
    if not filepath or not os.path.exists(filepath):
        return proxies_list
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            for raw in f:
                line = raw.strip()
                if not line or line.startswith("#"):
                    continue
                proxies_list.append(line)
    except Exception as e:
        print(f"[Error] 加载代理文件失败 ({filepath}): {e}")
    return proxies_list


class ProxyRotator:
    """线程安全的代理轮换器 (round-robin)"""

    def __init__(self, proxy_list: List[str]):
        self._proxies = list(proxy_list) if proxy_list else []
        self._index = 0
        self._lock = threading.Lock()

    def __len__(self) -> int:
        return len(self._proxies)

    def next(self) -> Optional[str]:
        if not self._proxies:
            return None
        with self._lock:
            proxy = self._proxies[self._index % len(self._proxies)]
            self._index += 1
            return proxy


class EmailQueue:
    """线程安全的邮箱队列，从文件逐行读取并消费"""

    def __init__(self, filepath: str):
        self._filepath = filepath
        self._emails: List[str] = []
        self._lock = threading.Lock()
        self._load()

    def _load(self) -> None:
        if not os.path.exists(self._filepath):
            return
        with open(self._filepath, "r", encoding="utf-8") as f:
            for line in f:
                addr = line.strip()
                if not addr or addr.startswith("#"):
                    continue
                if "----" in addr:
                    addr = addr.split("----")[0].strip()
                if addr and "@" in addr:
                    self._emails.append(addr)

    def pop(self) -> Optional[str]:
        with self._lock:
            if not self._emails:
                return None
            email = self._emails.pop(0)
            self._save_unlocked()
            return email

    def _save_unlocked(self) -> None:
        try:
            with open(self._filepath, "w", encoding="utf-8") as f:
                for email in self._emails:
                    f.write(email + "\n")
        except Exception:
            pass

    def __len__(self) -> int:
        with self._lock:
            return len(self._emails)


class LocalOutlookAccountQueue:
    """线程安全的本地 Outlook 账号队列，支持邮箱----密码----client_id----refresh_token"""

    def __init__(self, filepath: str):
        self._filepath = filepath
        self._accounts: List[dict] = []
        self._lock = threading.Lock()
        self._load()

    def _load(self) -> None:
        if not os.path.exists(self._filepath):
            return
        with open(self._filepath, "r", encoding="utf-8") as f:
            for raw in f:
                line = raw.strip()
                if not line or line.startswith("#"):
                    continue
                parts = [part.strip() for part in line.split("----", 3)]
                if len(parts) != 4:
                    continue
                email, password, client_id, refresh_token = parts
                if not email or "@" not in email or not client_id or not refresh_token:
                    continue
                self._accounts.append(
                    {
                        "email": email,
                        "password": password,
                        "client_id": client_id,
                        "refresh_token": refresh_token,
                    }
                )

    def pop(self) -> Optional[dict]:
        with self._lock:
            if not self._accounts:
                return None
            account = self._accounts.pop(0)
            self._save_unlocked()
            return account

    def _save_unlocked(self) -> None:
        try:
            with open(self._filepath, "w", encoding="utf-8") as f:
                for account in self._accounts:
                    f.write(
                        "----".join(
                            [
                                account.get("email", ""),
                                account.get("password", ""),
                                account.get("client_id", ""),
                                account.get("refresh_token", ""),
                            ]
                        )
                        + "\n"
                    )
        except Exception:
            pass

    def __len__(self) -> int:
        with self._lock:
            return len(self._accounts)


class RegistrationStats:
    """注册统计类，实时跟踪注册情况"""

    def __init__(self):
        self._lock = threading.Lock()
        self.start_time = time.time()
        self.total_attempts = 0
        self.success_count = 0
        self.fail_count = 0
        self.fail_reasons = {
            "403_forbidden": 0,
            "signup_form_error": 0,
            "password_error": 0,
            "otp_timeout": 0,
            "account_create_error": 0,
            "callback_error": 0,
            "network_error": 0,
            "other_error": 0,
        }
        self.last_10_results: List[bool] = []

    def add_attempt(self) -> None:
        with self._lock:
            self.total_attempts += 1

    def add_success(self) -> None:
        with self._lock:
            self.success_count += 1
            self.last_10_results.append(True)
            if len(self.last_10_results) > 10:
                self.last_10_results.pop(0)

    def add_failure(self, reason: str = "other_error") -> None:
        with self._lock:
            self.fail_count += 1
            if reason in self.fail_reasons:
                self.fail_reasons[reason] += 1
            else:
                self.fail_reasons["other_error"] += 1
            self.last_10_results.append(False)
            if len(self.last_10_results) > 10:
                self.last_10_results.pop(0)

    def get_stats(self) -> dict:
        with self._lock:
            elapsed = time.time() - self.start_time
            total = self.success_count + self.fail_count
            overall_rate = (self.success_count / total * 100) if total > 0 else 0
            recent_rate = (sum(self.last_10_results) / len(self.last_10_results) * 100) if self.last_10_results else 0
            speed = self.success_count / (elapsed / 3600) if elapsed > 0 else 0
            return {
                "elapsed_time": elapsed,
                "total_attempts": self.total_attempts,
                "success_count": self.success_count,
                "fail_count": self.fail_count,
                "overall_success_rate": overall_rate,
                "recent_success_rate": recent_rate,
                "speed_per_hour": speed,
                "fail_reasons": self.fail_reasons.copy(),
            }

    def format_display(self) -> str:
        stats = self.get_stats()
        elapsed = stats["elapsed_time"]
        hours = int(elapsed // 3600)
        minutes = int((elapsed % 3600) // 60)
        seconds = int(elapsed % 60)

        lines = [
            "",
            "=" * 60,
            " 📊 注册统计面板",
            "=" * 60,
            f" ⏱️  运行时间: {hours:02d}:{minutes:02d}:{seconds:02d}",
            f" 📈 总尝试数: {stats['total_attempts']}",
            f" ✅ 成功: {stats['success_count']} | ❌ 失败: {stats['fail_count']}",
            f" 📊 总体成功率: {stats['overall_success_rate']:.1f}%",
            f" 📊 最近10次成功率: {stats['recent_success_rate']:.1f}%",
            f" 🚀 速度: {stats['speed_per_hour']:.1f} 个/小时",
            "-" * 60,
            " 📉 失败原因分布:",
        ]

        for reason, count in stats["fail_reasons"].items():
            if count > 0:
                lines.append(f"    • {reason}: {count}")

        lines.append("=" * 60)
        return "\n".join(lines)

    def format_compact(self) -> str:
        stats = self.get_stats()
        elapsed = stats["elapsed_time"]
        hours = int(elapsed // 3600)
        minutes = int((elapsed % 3600) // 60)
        seconds = int(elapsed % 60)
        return (
            f"状态 | ⏱️ {hours:02d}:{minutes:02d}:{seconds:02d} | "
            f"尝试 {stats['total_attempts']} | "
            f"成功 {stats['success_count']} | "
            f"失败 {stats['fail_count']} | "
            f"总率 {stats['overall_success_rate']:.1f}% | "
            f"近10次 {stats['recent_success_rate']:.1f}% | "
            f"速度 {stats['speed_per_hour']:.1f}/h"
        )


class ActiveEmailQueue:
    """线程安全的活跃邮箱队列，存储预检测的活跃邮箱"""

    def __init__(self):
        self._emails: List[dict] = []
        self._lock = threading.Lock()

    def add_batch(self, emails: list) -> None:
        with self._lock:
            self._emails.extend(emails)

    def pop(self) -> Optional[dict]:
        with self._lock:
            if not self._emails:
                return None
            return self._emails.pop(0)

    def __len__(self) -> int:
        with self._lock:
            return len(self._emails)

    def is_empty(self) -> bool:
        with self._lock:
            return len(self._emails) == 0


_email_queue: Optional[EmailQueue] = None
_active_email_queue: Optional[ActiveEmailQueue] = None
_prefetch_no_stock = False
_prefetch_lock = threading.Lock()
_luckmail_purchased_only = False
_luckmail_skip_purchased = False
_luckmail_own_only = False
_reg_stats: Optional[RegistrationStats] = None
_stats_last_line = ""

_hotmail007_credentials: Dict[str, dict] = {}
_luckmail_credentials: Dict[str, dict] = {}

_file_write_lock = threading.Lock()
_success_counter_lock = threading.Lock()
_success_counter = 0

_INVALID_ERRORS = {
    "account_deactivated", "invalid_api_key", "user_deactivated",
    "account_banned", "invalid_grant",
}



def _ssl_verify() -> bool:
    return True



def _skip_net_check() -> bool:
    return False



def build_proxies(proxy: Optional[str]):
    return {"http": proxy, "https": proxy} if proxy else None
