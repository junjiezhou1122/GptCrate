# OpenAI 自动注册工具

自动批量注册 OpenAI 账号，支持多代理轮换、多线程并发，并支持接入自建 OpenTrashmail 临时邮箱服务。

---

## 推荐：使用 LuckMail 接码平台

**强烈推荐使用 [LuckMail](https://mails.luckyous.com/EC36F88F) 接码平台！**

- 注册地址：https://mails.luckyous.com/EC36F88F
- 只需填写 API Key，其他配置代码已帮你搞定！
- 自动获取邮箱、自动接收验证码、全自动注册

---

## 快速开始

```bash
# 1. 复制配置模板
cp .env.example .env

# 2. 编辑 .env，填写 LuckMail API Key
# LUCKMAIL_API_KEY=你的API密钥

# 3. 运行
python gpt.py --once
```

或使用一键启动器（推荐）：

```bash
python start.py
```

---

## 环境要求

- Python 3.8+
- 依赖安装：

```bash
pip install curl_cffi
```

---

## 文件说明

| 文件          | 作用                              |
| ------------- | --------------------------------- |
| `gpt.py`      | 主程序                            |
| `.env`        | 配置文件 (邮箱、代理、输出路径等) |
| `proxies.txt` | 代理列表文件 (每行一个代理)       |

---

## 配置文件 (.env)

### 邮箱模式

支持四种邮箱来源，通过 `EMAIL_MODE` 切换：

| 模式              | 值           | 说明                                                                                        |
| ----------------- | ------------ | ------------------------------------------------------------------------------------------- |
| Cloudflare Worker | `cf`         | 使用自有域名随机生成邮箱，需配置 `MAIL_DOMAIN` / `MAIL_WORKER_BASE` / `MAIL_ADMIN_PASSWORD` |
| Hotmail007 API    | `hotmail007` | 通过 API 拉取微软邮箱，需配置 `HOTMAIL007_API_KEY`                                          |
| LuckMail API      | `luckmail`   | 通过接码平台自动获取邮箱，需配置 `LUCKMAIL_API_KEY`                                         |
| OpenTrashmail     | `opentrashmail` | 通过自建 OpenTrashmail 的 JSON API 收验证码，需配置 `OPENTRASHMAIL_BASE_URL` / `OPENTRASHMAIL_DOMAIN` |

**Cloudflare 模式配置：**

```env
EMAIL_MODE=cf
MAIL_DOMAIN=your-domain.com
MAIL_WORKER_BASE=https://mail-worker.your-domain.com
MAIL_ADMIN_PASSWORD=your-password
```

**Hotmail007 模式配置：**

```env
EMAIL_MODE=hotmail007
HOTMAIL007_API_URL=https://gapi.hotmail007.com
HOTMAIL007_API_KEY=你的API密钥
HOTMAIL007_MAIL_TYPE=outlook-premium
HOTMAIL007_MAIL_MODE=imap
```

`HOTMAIL007_MAIL_MODE` 支持 `graph` (Microsoft Graph API) 和 `imap` (IMAP 协议) 两种收信方式。

**LuckMail 模式配置：**

```env
EMAIL_MODE=luckmail
LUCKMAIL_API_URL=https://mails.luckyous.com/api/v1/openapi
LUCKMAIL_API_KEY=你的API密钥
```

LuckMail 模式会自动创建接码订单，每次注册都会分配一个新的 outlook 邮箱用于接收验证码。

**OpenTrashmail 模式配置：**

```env
EMAIL_MODE=opentrashmail
OPENTRASHMAIL_BASE_URL=https://mail.example.com
OPENTRASHMAIL_DOMAIN=mail.example.com
OPENTRASHMAIL_PASSWORD=
OPENTRASHMAIL_POLL_TIMEOUT=120
OPENTRASHMAIL_POLL_INTERVAL=3
```

- `OPENTRASHMAIL_BASE_URL` 填你的 OpenTrashmail 站点地址，不要带末尾 `/`
- `OPENTRASHMAIL_DOMAIN` 填 OpenTrashmail 接收邮件的域名；如配置了多个域名，可用逗号分隔
- `OPENTRASHMAIL_PASSWORD` 只有在 OpenTrashmail 配置了 `PASSWORD` 时才需要填写；程序会通过 `PWD` 请求头访问 API
- 程序会自动随机生成邮箱地址，并轮询 `/json/[email]` / `/json/[email]/[id]` 获取 OpenAI 验证码

### 代理配置

两种方式二选一：

```env
# 方式一：单代理
PROXY=http://127.0.0.1:7890

# 方式二：代理列表文件 (批量注册时自动轮换)
PROXY_FILE=proxies.txt
```

### 批量注册配置

```env
BATCH_COUNT=10
BATCH_THREADS=2
```

### 输出路径

```env
TOKEN_OUTPUT_DIR=./tokens
CLI_PROXY_AUTHS_DIR=/path/to/auths
```

- `TOKEN_OUTPUT_DIR` -- Token JSON 文件保存目录
- `CLI_PROXY_AUTHS_DIR` -- 若配置，注册成功后自动拷贝 token 到该目录并删除本地副本

---

## 代理列表文件 (proxies.txt)

每行一个代理地址，空行和 `#` 开头的注释行会被忽略。

```
# HTTP 代理
http://127.0.0.1:7890
http://user:pass@proxy1.com:8080

# SOCKS5 代理
socks5://127.0.0.1:1080
socks5://user:pass@proxy2.com:1080
```

批量注册时会按 **round-robin** 顺序自动轮换使用这些代理。

---

## 命令行参数

```
python gpt.py [参数]
```

| 参数                     | 默认值               | 说明                                        |
| ------------------------ | -------------------- | ------------------------------------------- |
| `--proxy`                | 无                   | 单个代理地址                                |
| `--proxy-file`           | 读 .env `PROXY_FILE` | 代理列表文件路径                            |
| `--count`                | 无 (无限循环)        | 批量注册数量，注册够了自动停止              |
| `--threads`              | 1                    | 并发线程数                                  |
| `--once`                 | -                    | 只运行一次 (等同 `--count 1`)               |
| `--check`                | -                    | 先检测已有 token 状态，不足阈值时自动补注册 |
| `--sleep-min`            | 5                    | 每次注册间隔最短秒数                        |
| `--sleep-max`            | 30                   | 每次注册间隔最长秒数                        |
| `--email-mode`           | 读 .env              | 邮箱模式: `cf` / `hotmail007` / `luckmail` / `opentrashmail`  |
| `--hotmail007-key`       | 读 .env              | 覆盖 .env 中的 Hotmail007 API Key           |
| `--hotmail007-type`      | 读 .env              | 覆盖 .env 中的邮箱类型                      |
| `--hotmail007-mail-mode` | 读 .env              | 收信模式: `graph` / `imap`                  |
| `--luckmail-key`         | 读 .env              | 覆盖 .env 中的 LuckMail API Key             |
| `--opentrashmail-url`    | 读 .env              | 覆盖 .env 中的 OpenTrashmail 服务地址       |
| `--opentrashmail-domain` | 读 .env              | 覆盖 .env 中的 OpenTrashmail 邮箱域名       |
| `--opentrashmail-password` | 读 .env            | 覆盖 .env 中的 OpenTrashmail API 密码       |

---

## 使用示例

### 1. 单次注册 (直连)

```bash
python gpt.py --once
```

### 2. 单代理注册一个

```bash
python gpt.py --proxy http://127.0.0.1:7890 --once
```

### 3. 单代理批量注册 10 个

```bash
python gpt.py --proxy http://127.0.0.1:7890 --count 10
```

### 4. 多代理轮换 + 批量注册

```bash
python gpt.py --proxy-file proxies.txt --count 20
```

### 5. 多代理 + 3 线程并发批量注册

```bash
python gpt.py --proxy-file proxies.txt --count 20 --threads 3
```

### 6. 全部配置写在 .env，直接运行

```env
PROXY_FILE=proxies.txt
BATCH_COUNT=10
BATCH_THREADS=2
```

```bash
python gpt.py
```

### 7. 检测已有 token + 自动补注册

```bash
python gpt.py --check --proxy-file proxies.txt
```

先扫描 `CLI_PROXY_AUTHS_DIR` 下的 token 文件，刷新过期的、删除无效的，可用数低于阈值 (默认 10) 时自动补注册。

### 8. 无限循环模式 (持续注册)

```bash
python gpt.py --proxy-file proxies.txt --threads 2
```

不指定 `--count` 时为无限循环模式，按 `Ctrl+C` 停止。

### 9. 使用 OpenTrashmail 自建邮箱

```bash
python gpt.py --email-mode opentrashmail \
  --opentrashmail-url https://mail.example.com \
  --opentrashmail-domain mail.example.com \
  --once
```

如果你的 OpenTrashmail 开启了 `PASSWORD`，再额外加上：

```bash
python gpt.py --email-mode opentrashmail \
  --opentrashmail-url https://mail.example.com \
  --opentrashmail-domain mail.example.com \
  --opentrashmail-password your-password \
  --once
```

---

## 输出文件

| 文件                        | 说明                                                               |
| --------------------------- | ------------------------------------------------------------------ |
| `token_xxx@xxx_时间戳.json` | 注册成功的 Token JSON (含 access_token / refresh_token / email 等) |
| `accounts.txt`              | 所有成功注册的账号密码，格式: `邮箱----密码`                       |

---

## 注意事项

1. 代理必须为非 CN/HK 地区的 IP，否则网络检查会拦截
2. 多线程数建议不超过代理数量，避免同一代理并发过多被风控
3. `Ctrl+C` 可随时优雅中断所有线程
4. `--sleep-min` / `--sleep-max` 控制注册间隔，防止频率过高
