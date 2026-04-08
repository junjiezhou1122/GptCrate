#!/usr/bin/env python3
"""
OpenAI 注册工具 - 一键启动器
"""

import os
import sys
from typing import Optional


def _prompt_choice(prompt: str, options: dict[str, str], invalid_message: str) -> str:
    while True:
        choice = input(prompt).strip()
        if choice in options:
            return options[choice]
        print(invalid_message)


def _prompt_positive_int(prompt: str, default: Optional[int] = None) -> int:
    while True:
        raw = input(prompt).strip()
        if not raw and default is not None:
            return default
        try:
            value = int(raw)
        except ValueError:
            print("请输入有效的数字")
            continue
        if value > 0:
            return value
        print("请输入大于 0 的数字")


def _prompt_optional_positive_int(prompt: str) -> Optional[int]:
    while True:
        raw = input(prompt).strip()
        if not raw:
            return None
        try:
            value = int(raw)
        except ValueError:
            print("请输入有效的数字")
            continue
        if value > 0:
            return value
        print("请输入大于 0 的数字")


def print_banner() -> None:
    print("=" * 50)
    print("   OpenAI 自动注册工具")
    print("   一键启动器")
    print("=" * 50)
    print()

def select_platform() -> str:
    print("请选择邮箱平台:")
    print("  1. LuckMail (推荐 - 自动接码，省心省力)")
    print("  2. Hotmail007 (需要已有微软邮箱)")
    print("  3. 本地 Outlook 导入 (使用 邮箱----密码----client_id----刷新令牌)")
    print("  4. 自建邮箱 / Cloudflare Worker")
    print()
    return _prompt_choice(
        "请输入选项 (1/2/3/4): ",
        {"1": "luckmail", "2": "hotmail007", "3": "local_outlook", "4": "cf"},
        "无效选项，请输入 1、2、3 或 4",
    )

def select_luckmail_mode() -> str:
    print("\n请选择 LuckMail 工作模式:")
    print("  1. 预检测模式 - 批量购买并检测活跃度 (推荐，需确保有库存)")
    print("  2. 实时购买模式 - 注册时实时购买并检测")
    print("  3. 接码模式 - 使用平台临时邮箱接收验证码")
    print("  4. 已购邮箱模式 - 使用已购买的邮箱，检测活跃度后使用")
    print("  5. 我的邮箱模式 - 只使用自己导入到 LuckMail 的邮箱")
    print()
    return _prompt_choice(
        "请输入选项 (1/2/3/4/5): ",
        {
            "1": "prefetch",
            "2": "realtime",
            "3": "order",
            "4": "purchased",
            "5": "own",
        },
        "无效选项，请输入 1、2、3、4 或 5",
    )

def select_email_type() -> str:
    print("\n请选择邮箱类型:")
    print("  1. ms_imap (IMAP协议接收邮件)")
    print("  2. ms_graph (Microsoft Graph API)")
    print()
    return _prompt_choice(
        "请输入选项 (1/2，默认1): ",
        {"": "ms_imap", "1": "ms_imap", "2": "ms_graph"},
        "无效选项，请输入 1 或 2",
    )

def select_local_outlook_mail_mode() -> str:
    print("\n请选择本地 Outlook 收信模式:")
    print("  1. graph (Microsoft Graph API，默认)")
    print("  2. imap (IMAP 协议)")
    print()
    return _prompt_choice(
        "请输入选项 (1/2，默认1): ",
        {"": "graph", "1": "graph", "2": "imap"},
        "无效选项，请输入 1 或 2",
    )

def _read_env_value(path: str, key: str) -> Optional[str]:
    if not os.path.exists(path):
        return None

    with open(path, "r", encoding="utf-8") as f:
        for raw_line in f:
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            current_key, value = line.split("=", 1)
            if current_key.strip() != key:
                continue
            value = value.strip()
            if "#" in value and not value.startswith(("'", '"')):
                value = value.split("#", 1)[0].strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
                value = value[1:-1]
            return value or None
    return None

def _prompt_required_with_default(prompt: str, default: Optional[str] = None) -> str:
    while True:
        suffix = f" [{default}]" if default else ""
        value = input(f"{prompt}{suffix}: ").strip()
        if value:
            return value
        if default:
            return default
        print("该项不能为空")

def get_api_key(platform: str) -> str:
    if platform in {"local_outlook", "cf"}:
        return ""

    # 先检查 .env 文件是否已有 API Key
    env_key = "LUCKMAIL_API_KEY" if platform == "luckmail" else "HOTMAIL007_API_KEY"
    existing_key = _read_env_value(".env", env_key)
    if existing_key and not existing_key.startswith("你的"):
        print(f"\n✅ 检测到已配置的 {platform} API Key，跳过输入")
        return existing_key

    # 如果没有配置，让用户输入
    if platform == "luckmail":
        print("\n请输入 LuckMail API Key:")
        print("(在你的 LuckMail 账户 -> API 中获取)")
    else:
        print("\n请输入 Hotmail007 API Key:")
        print("(在你的 Hotmail007 账户 -> API 中获取)")
    
    return input("请输入 API Key: ").strip()

def get_cf_config() -> tuple[str, str, str]:
    existing_domain = _read_env_value(".env", "MAIL_DOMAIN")
    existing_worker = _read_env_value(".env", "MAIL_WORKER_BASE")
    existing_password = _read_env_value(".env", "MAIL_ADMIN_PASSWORD")

    print("\n请输入自建邮箱 / Cloudflare Worker 配置:")
    domain = _prompt_required_with_default("MAIL_DOMAIN", existing_domain)
    worker_base = _prompt_required_with_default("MAIL_WORKER_BASE", existing_worker)
    admin_password = _prompt_required_with_default("MAIL_ADMIN_PASSWORD", existing_password)
    return domain, worker_base, admin_password

def get_count() -> Optional[int]:
    print("\n请输入要注册的账号数量:")
    print("(直接回车表示无限循环，直到 Ctrl+C 停止)")
    return _prompt_optional_positive_int("请输入数量 (或直接回车): ")

def get_threads() -> int:
    print("\n请输入并发线程数:")
    print("(建议 1-5，太高可能容易被封)")
    return _prompt_positive_int("请输入线程数 (默认 1): ", default=1)

def generate_env(
    platform: str,
    api_key: str,
    count: Optional[int],
    threads: int,
    luckmail_mode: str = "prefetch",
    email_type: str = "ms_imap",
    local_outlook_mail_mode: str = "graph",
    cf_domain: str = "",
    cf_worker_base: str = "",
    cf_admin_password: str = "",
) -> None:
    batch_count_line = f"BATCH_COUNT={count}" if count else "# BATCH_COUNT=10"
    batch_threads_line = f"BATCH_THREADS={threads}"
    env_content = f"""MAIL_DOMAIN={cf_domain}
MAIL_WORKER_BASE={cf_worker_base}
MAIL_ADMIN_PASSWORD={cf_admin_password}
TOKEN_OUTPUT_DIR=./tokens
CLI_PROXY_AUTHS_DIR=

PROXY_FILE=proxies.txt

# 批量注册配置
{batch_count_line}
{batch_threads_line}

# 邮箱模式
EMAIL_MODE={platform}
ACCOUNTS_FILE=accounts.txt
"""

    if platform == "luckmail":
        # 根据模式设置 LUCKMAIL_AUTO_BUY, LUCKMAIL_PURCHASED_ONLY, LUCKMAIL_SKIP_PURCHASED, LUCKMAIL_OWN_ONLY
        if luckmail_mode == "prefetch":
            auto_buy = "true"
            purchased_only = "false"
            skip_purchased = "true"  # 预检测模式：跳过已购邮箱，直接购买新邮箱
            own_only = "false"
        elif luckmail_mode == "realtime":
            auto_buy = "true"
            purchased_only = "false"
            skip_purchased = "false"  # 实时购买模式：可以尝试使用已购邮箱
            own_only = "false"
        elif luckmail_mode == "purchased":
            auto_buy = "true"  # 启用预检测逻辑
            purchased_only = "true"  # 只使用已购邮箱，不购买新邮箱
            skip_purchased = "false"
            own_only = "false"
        elif luckmail_mode == "own":
            auto_buy = "true"  # 启用预检测逻辑，读取“我的邮箱”池
            purchased_only = "false"
            skip_purchased = "true"
            own_only = "true"
        else:  # order 模式
            auto_buy = "false"
            purchased_only = "false"
            skip_purchased = "false"
            own_only = "false"

        env_content += f"""
# LuckMail 模式配置
LUCKMAIL_API_URL=https://mails.luckyous.com/api/v1/openapi
LUCKMAIL_API_KEY={api_key}
# 邮箱类型: ms_imap 或 ms_graph
LUCKMAIL_EMAIL_TYPE={email_type}
# 自动购买邮箱并检测活跃度（true=预检测/实时购买，false=接码模式）
LUCKMAIL_AUTO_BUY={auto_buy}
# 只使用已购邮箱模式（true=只用已购邮箱，用完停止；false=允许购买新邮箱）
LUCKMAIL_PURCHASED_ONLY={purchased_only}
# 跳过已购邮箱检查（true=跳过已购邮箱直接购买新邮箱；false=先检查已购邮箱）
LUCKMAIL_SKIP_PURCHASED={skip_purchased}
# 只使用自己导入到 LuckMail 的邮箱（true=只读“我的邮箱”，用完停止）
LUCKMAIL_OWN_ONLY={own_only}
# 已购/预检测邮箱活跃度检测并发数
LUCKMAIL_CHECK_WORKERS=20
# 邮箱不活跃时的最大重试次数
LUCKMAIL_MAX_RETRY=3
"""
    elif platform == "hotmail007":
        env_content += f"""
# Hotmail007 模式配置
HOTMAIL007_API_URL=https://gapi.hotmail007.com
HOTMAIL007_API_KEY={api_key}
HOTMAIL007_MAIL_TYPE=hotmail Trusted Graph
HOTMAIL007_MAIL_MODE=imap
"""
    elif platform == "cf":
        env_content += """
# 自建邮箱 / Cloudflare Worker 模式
"""
    else:
        env_content += f"""
# 本地 Outlook 导入模式
# accounts.txt 每行格式: 邮箱----密码----client_id----refresh_token
LOCAL_OUTLOOK_MAIL_MODE={local_outlook_mail_mode}
LOCAL_OUTLOOK_BAD_FILE=bad_local_outlook.txt
"""

    with open(".env", "w", encoding="utf-8") as f:
        f.write(env_content)

def run_gpt(count: Optional[int], threads: int) -> None:
    cmd = ["gpt.py"]
    
    if count:
        cmd.extend(["--count", str(count)])
    
    if threads > 1:
        cmd.extend(["--threads", str(threads)])
    
    cmd.append("--proxy-file")
    cmd.append("proxies.txt")
    
    print("\n" + "=" * 50)
    print("开始运行 OpenAI 注册工具...")
    print("=" * 50 + "\n")
    
    os.execv(sys.executable, [sys.executable] + cmd)

def main() -> None:
    print_banner()

    # 检查环境
    if not os.path.exists(".env.example"):
        print("错误: 未找到 .env.example 文件")
        sys.exit(1)

    if not os.path.exists("gpt.py"):
        print("错误: 未找到 gpt.py 文件")
        sys.exit(1)

    # 选择平台
    platform = select_platform()

    # LuckMail 额外选项
    luckmail_mode = "prefetch"
    email_type = "ms_imap"
    local_outlook_mail_mode = "graph"
    cf_domain = ""
    cf_worker_base = ""
    cf_admin_password = ""
    if platform == "luckmail":
        luckmail_mode = select_luckmail_mode()
        if luckmail_mode in ["prefetch", "realtime"]:
            email_type = select_email_type()
    elif platform == "local_outlook":
        local_outlook_mail_mode = select_local_outlook_mail_mode()
    elif platform == "cf":
        cf_domain, cf_worker_base, cf_admin_password = get_cf_config()

    # 获取 API Key
    api_key = get_api_key(platform)
    if platform != "local_outlook" and not api_key:
        print("错误: API Key 不能为空")
        sys.exit(1)

    # 获取数量
    count = get_count()

    # 获取并发数
    threads = get_threads()

    # 生成配置
    generate_env(
        platform,
        api_key,
        count,
        threads,
        luckmail_mode,
        email_type,
        local_outlook_mail_mode,
        cf_domain,
        cf_worker_base,
        cf_admin_password,
    )
    print("\n✅ 配置文件已生成!")

    # 运行
    run_gpt(count, threads)

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n已退出")
        sys.exit(0)
