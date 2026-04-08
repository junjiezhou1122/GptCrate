# OpenAI 自动注册工具

> 免责声明
>
> 本项目仅供学习与技术研究使用，请勿用于任何违反服务条款、法律法规或他人权益的用途。

自动批量注册 OpenAI 账号，支持多代理轮换、多线程并发。

---

> 默认推荐方案：`LuckMail`
>
> 这套 README 的快速开始、默认示例和一键启动器都优先按 `LuckMail` 配置。

## 首页推荐：使用 LuckMail 接码平台

**强烈推荐使用 [LuckMail](https://mails.luckyous.com/EC36F88F) 接码平台！**

- 注册地址：https://mails.luckyous.com/EC36F88F
- 只需填写 API Key，其他配置代码已帮你搞定！
- **智能预检测**：自动购买邮箱并检测活跃度，只使用活跃邮箱注册
- **自动禁用**：不活跃邮箱自动禁用，避免浪费

最小配置：

```env
EMAIL_MODE=luckmail
LUCKMAIL_API_KEY=你的API密钥
TOKEN_OUTPUT_DIR=./tokens
```

---

## 代理推荐

**高质量代理服务商，支持免费测试，先充值后付费**

- 微信: `ytip886`
- Telegram: `yitong886`

![代理产品](./image.png)

---

## 快速开始

```bash
# 1. 安装 uv
# macOS / Linux
curl -LsSf https://astral.sh/uv/install.sh | sh

# Windows PowerShell
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"

# 2. 创建虚拟环境并安装依赖
uv sync

# 3. 复制配置模板
cp .env.example .env

# 4. 编辑 .env，填写 LuckMail API Key
# LUCKMAIL_API_KEY=你的API密钥

# 5. 运行
uv run python gpt.py --once
```

或使用一键启动器（推荐）：

```bash
uv run python start.py
```

---

## 环境要求

- 推荐使用 `uv` 自动管理 Python 版本和虚拟环境
- 项目内置 `.python-version`，默认使用 Python 3.11
- 若不用 `uv`，也可以手动准备 Python 3.10+

### 使用 uv

```bash
uv sync
```

首次执行会自动创建 `.venv/` 并安装依赖。

### 手动安装依赖

```bash
pip install curl_cffi
```

---

## 文件说明

| 文件          | 作用                              |
| ------------- | --------------------------------- |
| `gpt.py`      | 主程序                            |
| `start.py`    | 一键启动器（带交互式配置）        |
| `.env`        | 配置文件 (邮箱、代理、输出路径等) |
| `proxies.txt` | 代理列表文件 (每行一个代理)       |

---

## 配置文件 (.env)

### 邮箱模式

支持三种邮箱来源，通过 `EMAIL_MODE` 切换：

| 模式              | 值           | 说明                                                                                        |
| ----------------- | ------------ | ------------------------------------------------------------------------------------------- |
| Cloudflare Worker | `cf`         | 使用自有域名随机生成邮箱，需配置 `MAIL_DOMAIN` / `MAIL_WORKER_BASE` / `MAIL_ADMIN_PASSWORD` |
| Hotmail007 API    | `hotmail007` | 通过 API 拉取微软邮箱，需配置 `HOTMAIL007_API_KEY`                                          |
| **LuckMail API**  | `luckmail`   | **推荐** 智能购买+预检测活跃邮箱，需配置 `LUCKMAIL_API_KEY`                                 |

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

**LuckMail 模式配置（推荐）：**

```env
EMAIL_MODE=luckmail
LUCKMAIL_API_URL=https://mails.luckyous.com/api/v1/openapi
LUCKMAIL_API_KEY=你的API密钥

# 邮箱类型: ms_imap (IMAP协议) 或 ms_graph (Microsoft Graph API)
LUCKMAIL_EMAIL_TYPE=ms_imap
# 自动购买邮箱并检测活跃度（推荐开启）
LUCKMAIL_AUTO_BUY=true
# 只使用自己导入到 LuckMail 的邮箱（true=只读“我的邮箱”，用完即停）
LUCKMAIL_OWN_ONLY=false
# 已购/预检测邮箱活跃度检测并发数
LUCKMAIL_CHECK_WORKERS=20
# 调试 LuckMail 收信轮询（建议排查收不到验证码时临时开启）
LUCKMAIL_MAIL_DEBUG=false
# 邮箱不活跃时的最大重试次数
LUCKMAIL_MAX_RETRY=3
```

**LuckMail 工作模式说明：**

1. **预检测模式** (`LUCKMAIL_AUTO_BUY=true`)：
   - 启动时自动创建后台线程
   - **优先检查已购邮箱**：获取用户已购买的非禁用邮箱，默认只筛选 `@hotmail.com`
   - 批量购买新邮箱（默认20个）补充号池
   - **并行检测活跃度**（默认 `20` 线程，可用 `LUCKMAIL_CHECK_WORKERS` 调整）
   - **检测通过即刻入队**，不必等待整批检测结束
   - **自动禁用不活跃邮箱**
   - 注册时直接从队列取活跃邮箱使用
   - 队列不足时自动补充

2. **实时购买模式** (`LUCKMAIL_AUTO_BUY=true`，跳过预检测)：
   - 注册时实时购买邮箱
   - 购买后立即检测活跃度
   - 不活跃则禁用并重新购买

3. **接码模式** (`LUCKMAIL_AUTO_BUY=false`)：
   - 每次注册时创建接码订单
   - 平台自动分配临时邮箱
   - 适合快速测试

4. **已购邮箱模式**（推荐已有大量邮箱的用户）：
   - 只使用用户已购买的邮箱
   - **当前默认只使用 `@hotmail.com` 的已购邮箱**
   - 启动时批量检测已购邮箱活跃度，**谁先检测通过谁先入队**
   - 活跃的加入号池，不活跃的自动禁用
   - 若首轮没有筛到可用 Hotmail 活跃邮箱，则直接停止，不再空转线程

5. **我的邮箱模式**（适合已把 Outlook/Hotmail 导入 LuckMail 的用户）：
   - 只使用用户在 **“我的邮箱”** 中导入的邮箱
   - 启动时批量读取状态正常的邮箱并加入号池
   - 注册时直接从号池取邮箱，并通过对应邮箱邮件列表轮询验证码
   - 用完后直接停止，不购买新邮箱

### 代理配置

支持普通代理和 Resin 粘性代理两种方式：

```env
# 方式一：普通代理
PROXY=http://127.0.0.1:7890

# 方式二：代理列表文件 (批量注册时自动轮换)
PROXY_FILE=proxies.txt

# 方式三：Resin 粘性代理（整个注册流程使用同一个出口 IP）
RESIN_URL=http://token@resin-host:2260
RESIN_PLATFORM=Default
RESIN_STICKY=true
```

**Resin 粘性代理说明：**
- 启用后，整个注册流程（邮箱创建、OAuth、验证码、Token交换）都通过同一个代理连接
- Resin 会将该 Account 绑定到同一个出口 IP，实现粘性会话
- 适合需要固定出口 IP 的场景，避免 IP 变化导致风控

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
- 默认值为 `./tokens`，目录不存在时会自动创建
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

`uv run python gpt.py [参数]`

| 参数                     | 默认值               | 说明                                        |
| ------------------------ | -------------------- | ------------------------------------------- |
| `--proxy`                | 无                   | 单个代理地址或 Resin 网关地址              |
| `--proxy-file`           | 读 .env `PROXY_FILE` | 代理列表文件路径                            |
| `--resin-sticky`         | 读 .env `RESIN_STICKY` | 启用 Resin 粘性代理                        |
| `--resin-platform`       | 读 .env `RESIN_PLATFORM` | Resin Platform 名称                      |
| `--count`                | 无 (无限循环)        | 批量注册数量，注册够了自动停止              |
| `--threads`              | 1                    | 并发线程数                                  |
| `--once`                 | -                    | 只运行一次 (等同 `--count 1`)               |
| `--check`                | -                    | 先检测已有 token 状态，不足阈值时自动补注册 |
| `--sleep-min`            | 5                    | 每次注册间隔最短秒数                        |
| `--sleep-max`            | 30                   | 每次注册间隔最长秒数                        |
| `--email-mode`           | 读 .env              | 邮箱模式: `cf` / `hotmail007` / `luckmail`  |
| `--hotmail007-key`       | 读 .env              | 覆盖 .env 中的 Hotmail007 API Key           |
| `--hotmail007-type`      | 读 .env              | 覆盖 .env 中的邮箱类型                      |
| `--hotmail007-mail-mode` | 读 .env              | 收信模式: `graph` / `imap`                  |
| `--luckmail-key`         | 读 .env              | 覆盖 .env 中的 LuckMail API Key             |

---

## 使用示例

### 1. 单次注册 (直连)

```bash
uv run python gpt.py --once
```

### 2. 单代理注册一个

```bash
uv run python gpt.py --proxy http://127.0.0.1:7890 --once
```

### 3. 单代理批量注册 10 个

```bash
uv run python gpt.py --proxy http://127.0.0.1:7890 --count 10
```

### 4. 多代理轮换 + 批量注册

```bash
uv run python gpt.py --proxy-file proxies.txt --count 20
```

### 5. 多代理 + 3 线程并发批量注册

```bash
uv run python gpt.py --proxy-file proxies.txt --count 20 --threads 3
```

### 6. 全部配置写在 .env，直接运行

```env
PROXY_FILE=proxies.txt
BATCH_COUNT=10
BATCH_THREADS=2
```

```bash
uv run python gpt.py
```

### 7. Resin 粘性代理注册

```env
# 方式一：.env 配置
RESIN_URL=http://token@resin-host:2260
RESIN_PLATFORM=US
RESIN_STICKY=true
```

```bash
uv run python gpt.py --count 10

# 方式二：命令行参数
uv run python gpt.py --proxy http://token@resin-host:2260 --resin-sticky --resin-platform US --count 10
```

### 8. 检测已有 token + 自动补注册

```bash
uv run python gpt.py --check --proxy-file proxies.txt
```

先扫描 `CLI_PROXY_AUTHS_DIR` 下的 token 文件，刷新过期的、删除无效的，可用数低于阈值 (默认 10) 时自动补注册。

### 9. 无限循环模式 (持续注册)

```bash
uv run python gpt.py --proxy-file proxies.txt --threads 2
```

不指定 `--count` 时为无限循环模式，按 `Ctrl+C` 停止。

---

## 输出示例

### LuckMail 预检测模式输出（含已购邮箱检测）

```
[*] 启动预检测后台线程，维护活跃邮箱池...
[*] 等待预检测线程准备活跃邮箱...

[*] [预检测] 首先检查已购邮箱...
[*] 获取已购邮箱列表...
[*] 获取到 50 个已购邮箱，开始检测活跃度...
[*] 已购邮箱检测完成: ✓活跃 35/50 个, 已禁用 15 个不活跃邮箱
[*] [预检测] ✓ 已从已购邮箱中添加 35 个活跃邮箱 | 队列: 35 个
==================================================
[*] [预检测] 活跃邮箱池不足 (35/10)，批量购买 20 个...
==================================================
[*] 批量购买 20 个邮箱 (类型: ms_imap)...
[*] 成功购买 20 个邮箱，开始并行检测活跃度...
[*] 检测完成: ✓活跃 3 个, ✗不活跃 17 个(已禁用17个)
[*] 活跃邮箱列表:
    ✓ example1@hotmail.com
    ✓ example2@hotmail.com
    ✓ example3@hotmail.com
[*] [预检测] ✓ 已补充 3 个活跃邮箱 | 队列: 38 个

[T1#1] [12:28:05] 开始注册 (代理: http://127.0.0.1:1082)
[*] 当前 IP 所在地: JP
[*] ✓ 使用预检测活跃邮箱: example1@hotmail.com
[*] 活跃邮箱池: 37 个待使用
...
```

---

## 输出文件

| 文件                        | 说明                                                               |
| --------------------------- | ------------------------------------------------------------------ |
| `token_xxx@xxx_时间戳.json` | 注册成功的 Token JSON (含 access_token / refresh_token / email 等) |
| `accounts.txt`              | 所有成功注册的账号密码，格式: `邮箱----密码`                       |

---

## 注意事项

1. **代理必须为非 CN/HK 地区的 IP**，否则网络检查会拦截
2. **多线程数建议不超过代理数量**，避免同一代理并发过多被风控
3. `Ctrl+C` 可随时优雅中断所有线程
4. `--sleep-min` / `--sleep-max` 控制注册间隔，防止频率过高
5. **LuckMail 预检测模式**会优先检测已购邮箱活跃度，再批量购买新邮箱补充号池
6. **已购邮箱模式**当前默认只筛选 `@hotmail.com`，并把检测通过的邮箱立即加入号池
7. 可用 `LUCKMAIL_CHECK_WORKERS` 调整已购/预检测活跃度检测并发数（默认 `20`）
8. 排查 LuckMail 收不到验证码时，可临时开启 `LUCKMAIL_MAIL_DEBUG=true` 查看 `token/mails` 摘要、最近邮件主题与 fallback 状态
