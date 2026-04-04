#!/usr/bin/env python3
"""
OpenAI 注册工具 - 一键启动器
"""

import os
import sys
import shutil

def print_banner():
    print("=" * 50)
    print("   OpenAI 自动注册工具")
    print("   一键启动器")
    print("=" * 50)
    print()

def select_platform():
    print("请选择邮箱平台:")
    print("  1. LuckMail (推荐 - 自动接码，省心省力)")
    print("  2. Hotmail007 (需要已有微软邮箱)")
    print("  3. OpenTrashmail (自建临时邮箱服务)")
    print()
    
    while True:
        choice = input("请输入选项 (1/2/3): ").strip()
        if choice == "1":
            return "luckmail"
        elif choice == "2":
            return "hotmail007"
        elif choice == "3":
            return "opentrashmail"
        else:
            print("无效选项，请输入 1、2 或 3")

def get_env_value(env_key):
    if os.path.exists(".env"):
        with open(".env", "r", encoding="utf-8") as f:
            for line in f:
                if line.strip().startswith(env_key + "="):
                    existing_key = line.split("=", 1)[1].strip()
                    if existing_key and not existing_key.startswith("你的"):
                        return existing_key
    return ""

def get_platform_config(platform):
    if platform == "luckmail":
        existing_key = get_env_value("LUCKMAIL_API_KEY")
        if existing_key:
            print("\n✅ 检测到已配置的 luckmail API Key，跳过输入")
            return {"api_key": existing_key}
        print("\n请输入 LuckMail API Key:")
        print("(在你的 LuckMail 账户 -> API 中获取)")
        api_key = input("请输入 API Key: ").strip()
        return {"api_key": api_key}
    if platform == "hotmail007":
        existing_key = get_env_value("HOTMAIL007_API_KEY")
        if existing_key:
            print("\n✅ 检测到已配置的 hotmail007 API Key，跳过输入")
            return {"api_key": existing_key}
        print("\n请输入 Hotmail007 API Key:")
        print("(在你的 Hotmail007 账户 -> API 中获取)")
        api_key = input("请输入 API Key: ").strip()
        return {"api_key": api_key}

    base_url = get_env_value("OPENTRASHMAIL_BASE_URL")
    domain = get_env_value("OPENTRASHMAIL_DOMAIN")
    password = get_env_value("OPENTRASHMAIL_PASSWORD")

    if base_url:
        print("\n✅ 检测到已配置的 OpenTrashmail 服务地址，跳过输入")
    else:
        print("\n请输入 OpenTrashmail 服务地址:")
        print("(例如 https://mail.example.com)")
        base_url = input("请输入服务地址: ").strip().rstrip("/")

    if domain:
        print("✅ 检测到已配置的 OpenTrashmail 邮箱域名，跳过输入")
    else:
        print("\n请输入 OpenTrashmail 邮箱域名:")
        print("(例如 mail.example.com)")
        domain = input("请输入邮箱域名: ").strip()

    if password:
        print("✅ 检测到已配置的 OpenTrashmail API 密码，跳过输入")
    else:
        print("\n请输入 OpenTrashmail API 密码:")
        print("(如果未设置 PASSWORD，可直接回车留空)")
        password = input("请输入 API 密码: ").strip()

    return {
        "base_url": base_url,
        "domain": domain,
        "password": password,
    }

def get_count():
    print("\n请输入要注册的账号数量:")
    print("(直接回车表示无限循环，直到 Ctrl+C 停止)")
    
    while True:
        count_input = input("请输入数量 (或直接回车): ").strip()
        if not count_input:
            return None
        try:
            count = int(count_input)
            if count > 0:
                return count
            else:
                print("请输入大于 0 的数字")
        except ValueError:
            print("请输入有效的数字")

def get_threads():
    print("\n请输入并发线程数:")
    print("(建议 1-5，太高可能容易被封)")
    
    while True:
        threads_input = input("请输入线程数 (默认 1): ").strip()
        if not threads_input:
            return 1
        try:
            threads = int(threads_input)
            if threads > 0:
                return threads
            else:
                print("请输入大于 0 的数字")
        except ValueError:
            print("请输入有效的数字")

def generate_env(platform, platform_config, count, threads):
    env_content = f"""MAIL_DOMAIN=
MAIL_WORKER_BASE=
MAIL_ADMIN_PASSWORD=
TOKEN_OUTPUT_DIR=
CLI_PROXY_AUTHS_DIR=

PROXY_FILE=proxies.txt

# 批量注册配置
# BATCH_COUNT=10
# BATCH_THREADS=1

# 邮箱模式
EMAIL_MODE={platform}
ACCOUNTS_FILE=accounts.txt
"""
    
    if platform == "luckmail":
        env_content += f"""
# LuckMail 模式配置
LUCKMAIL_API_URL=https://mails.luckyous.com/api/v1/openapi
LUCKMAIL_API_KEY={platform_config["api_key"]}
"""
    elif platform == "hotmail007":
        env_content += f"""
# Hotmail007 模式配置
HOTMAIL007_API_URL=https://gapi.hotmail007.com
HOTMAIL007_API_KEY={platform_config["api_key"]}
HOTMAIL007_MAIL_TYPE=hotmail Trusted Graph
HOTMAIL007_MAIL_MODE=imap
"""
    else:
        env_content += f"""
# OpenTrashmail 模式配置
OPENTRASHMAIL_BASE_URL={platform_config["base_url"]}
OPENTRASHMAIL_DOMAIN={platform_config["domain"]}
OPENTRASHMAIL_PASSWORD={platform_config["password"]}
"""
    
    with open(".env", "w", encoding="utf-8") as f:
        f.write(env_content)

def run_gpt(count, threads):
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

def main():
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
    
    # 获取 API Key
    platform_config = get_platform_config(platform)
    if platform in ("luckmail", "hotmail007"):
        if not platform_config.get("api_key"):
            print("错误: API Key 不能为空")
            sys.exit(1)
    else:
        if not platform_config.get("base_url") or not platform_config.get("domain"):
            print("错误: OpenTrashmail 服务地址和邮箱域名不能为空")
            sys.exit(1)
    
    # 获取数量
    count = get_count()
    
    # 获取并发数
    threads = get_threads()
    
    # 生成配置
    generate_env(platform, platform_config, count, threads)
    print("\n✅ 配置文件已生成!")
    
    # 运行
    run_gpt(count, threads)

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n已退出")
        sys.exit(0)
