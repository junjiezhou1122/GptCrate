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
    print()
    
    while True:
        choice = input("请输入选项 (1/2): ").strip()
        if choice == "1":
            return "luckmail"
        elif choice == "2":
            return "hotmail007"
        else:
            print("无效选项，请输入 1 或 2")

def get_api_key(platform):
    # 先检查 .env 文件是否已有 API Key
    env_key = None
    if platform == "luckmail":
        env_key = "LUCKMAIL_API_KEY"
    else:
        env_key = "HOTMAIL007_API_KEY"
    
    if os.path.exists(".env"):
        with open(".env", "r", encoding="utf-8") as f:
            for line in f:
                if line.strip().startswith(env_key + "="):
                    existing_key = line.split("=", 1)[1].strip()
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
    
    api_key = input("请输入 API Key: ").strip()
    return api_key

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

def generate_env(platform, api_key, count, threads):
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
LUCKMAIL_API_KEY={api_key}
"""
    else:
        env_content += f"""
# Hotmail007 模式配置
HOTMAIL007_API_URL=https://gapi.hotmail007.com
HOTMAIL007_API_KEY={api_key}
HOTMAIL007_MAIL_TYPE=hotmail Trusted Graph
HOTMAIL007_MAIL_MODE=imap
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
    api_key = get_api_key(platform)
    if not api_key:
        print("错误: API Key 不能为空")
        sys.exit(1)
    
    # 获取数量
    count = get_count()
    
    # 获取并发数
    threads = get_threads()
    
    # 生成配置
    generate_env(platform, api_key, count, threads)
    print("\n✅ 配置文件已生成!")
    
    # 运行
    run_gpt(count, threads)

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n已退出")
        sys.exit(0)
