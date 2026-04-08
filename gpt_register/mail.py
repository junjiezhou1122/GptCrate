from typing import Any

from . import context as ctx
from .cf_mail import delete_temp_email as delete_cf_temp_email
from .cf_mail import extract_otp_code as _extract_otp_code
from .cf_mail import generate_email as get_cf_email_and_token
from .cf_mail import get_oai_code as get_cf_oai_code
from .hotmail import _outlook_fetch_otp, _outlook_get_known_ids, hotmail007_get_balance, hotmail007_get_mail, hotmail007_get_stock
from .hotmail import delete_temp_email as delete_hotmail_temp_email
from .hotmail import get_email_and_token as get_hotmail_email_and_token
from .hotmail import get_local_email_and_token as get_local_outlook_email_and_token
from .hotmail import get_oai_code as get_hotmail_oai_code
from .luckmail import _prefetch_active_emails, luckmail_batch_buy_and_check, luckmail_buy_email, luckmail_check_email_alive, luckmail_check_purchased_emails, luckmail_create_order, luckmail_disable_email, luckmail_get_all_purchased_emails, luckmail_get_code, luckmail_get_code_by_token, luckmail_get_purchased_emails, luckmail_get_purchases
from .luckmail import delete_temp_email as delete_luckmail_temp_email
from .luckmail import get_email_and_token as get_luckmail_email_and_token
from .luckmail import get_oai_code as get_luckmail_oai_code


def get_email_and_token(proxies: Any = None) -> tuple:
    """根据 EMAIL_MODE 获取邮箱，保留统一入口。"""
    if ctx.EMAIL_MODE == "file":
        if ctx._email_queue is None:
            print("[Error] 邮箱队列未初始化")
            return "", ""
        email = ctx._email_queue.pop()
        if not email:
            print("[Error] accounts.txt 中没有可用的邮箱了")
            return "", ""
        print(f"[*] 从文件读取邮箱: {email} (剩余: {len(ctx._email_queue)})")
        return email, email
    if ctx.EMAIL_MODE == "hotmail007":
        return get_hotmail_email_and_token(proxies=proxies)
    if ctx.EMAIL_MODE == "local_outlook":
        return get_local_outlook_email_and_token(proxies=proxies)
    if ctx.EMAIL_MODE == "luckmail":
        return get_luckmail_email_and_token(proxies=proxies)
    return get_cf_email_and_token()


def get_oai_code(token: str, email: str, proxies: Any = None, seen_ids: set = None) -> str:
    del token
    if ctx.EMAIL_MODE == "hotmail007":
        return get_hotmail_oai_code(email=email, proxies=proxies)
    if ctx.EMAIL_MODE == "local_outlook":
        return get_hotmail_oai_code(email=email, proxies=proxies)
    if ctx.EMAIL_MODE == "luckmail":
        return get_luckmail_oai_code(email=email, proxies=proxies, seen_ids=seen_ids)
    return get_cf_oai_code(email=email, proxies=proxies, seen_ids=seen_ids)


def delete_temp_email(email: str, proxies: Any = None) -> None:
    if ctx.EMAIL_MODE == "hotmail007":
        delete_hotmail_temp_email(email, proxies=proxies)
        return
    if ctx.EMAIL_MODE == "local_outlook":
        delete_hotmail_temp_email(email, proxies=proxies)
        return
    if ctx.EMAIL_MODE == "luckmail":
        delete_luckmail_temp_email(email, proxies=proxies)
        return
    delete_cf_temp_email(email, proxies=proxies)
