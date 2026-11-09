# CloudflareSpeedTest 优选 IP 自动同步模块 (.custom)

基于 `uv` 标准结构的 `CloudflareSpeedTest` 自动化扩展模块，自动筛选多地区低延迟 Anycast IP 并同步至 Cloudflare 泛解析（如 `*.cf.example.com`）。

## 核心特性

- **纯净测速与跨地区轮询打散**：默认采用轻量稳定的 TCPing 延迟测速（数十毫秒极速完成，零 TLS 握手开销），不预设地域壁垒，自动按实际路由测出最优节点，并按机房与 `/24` 子网防扎堆（`max_per_subnet: 2`）轮询抽取，防止单一 C 段垄断结果池。
- **动态达标测速省流**：通过 `-sl` 速度下限保障真实下载带宽，仅对优质候选 IP 执行 4 秒下载探活，12 小时周期下月均流量仅约 `50~70 GB`。
- **四重安全熔断**：API 预检探活、最低合格 IP 数量熔断（`< min_qualified_ips` 放弃更新）、先增后删（Add-before-Delete 平滑切换）、测速异常保留线上记录。
- **Webhook 结果推送**：默认对接飞书（Lark）自定义机器人（支持签名校验与交互式卡片），兼容钉钉 / 企业微信 / 通用 JSON Webhook；定时任务完成后自动推送状态、IP 列表、变更数与耗时，推送失败不影响主流程。
- **零三方依赖**：基于 Python 标准库实现，支持 `uv run` 与原生 `python3` 直接运行。

## 目录结构

```text
.custom/
├── uv-py/                     # uv Python 项目 (src-layout)
│   ├── pyproject.toml         # 项目元数据与 CLI 入口 (cfst-ddns)
│   ├── uv.lock
│   ├── src/cfst_ddns/         # 核心实现 (cloudflare / filter / speedtest / sync / notify / main)
│   └── tests/test_suite.py    # 单元测试套件
├── config.json.example        # 配置模板
├── deploy.sh                  # 环境部署与 systemd 安装脚本
├── systemd/                   # systemd 服务与 12h 定时器
└── worker/                    # Cloudflare Worker 随机泛域名前端与 /download 测速端点
```

## 快速开始

```bash
# 1. 初始化配置 (填入 api_token, zone_name, record_names, test_url)
cp .custom/config.json.example .custom/config.json
nano .custom/config.json

# 2. 部署 cfst 二进制与权限
bash .custom/deploy.sh

# 3. 校验 Token 权限 / 试运行 / 正式同步
uv run --project .custom/uv-py cfst-ddns --verify-only
uv run --project .custom/uv-py cfst-ddns --test-notify   # 验证飞书 Webhook 是否可用
uv run --project .custom/uv-py cfst-ddns --dry-run
uv run --project .custom/uv-py cfst-ddns

# 4. 安装或卸载 systemd 定时任务 (默认每 12 小时执行)
sudo bash .custom/deploy.sh --install-systemd
sudo bash .custom/deploy.sh --uninstall-systemd
```

## 配置说明 (`config.json`)

```json
{
  "cloudflare": {
    "api_token": "YOUR_CLOUDFLARE_API_TOKEN", // 需具备 Zone -> DNS -> Edit 权限
    "zone_name": "example.com",
    "zone_id": "",                             // 留空自动查询
    "record_names": ["*.cf.example.com"],
    "record_type": "A",
    "ttl": 60,
    "proxied": false,
    "comment": "Auto-updated by CloudflareSpeedTest"
  },
  "speedtest": {
    "binary_path": "cfst",
    "ip_file": "ip.txt",
    "specified_ips": [],
    "test_url": "https://cf.example.com/download",
    "port": 443,
    "thread_count": 64,                        // 测速并发线程数 (家宽建议 64~100)
    "ping_times": 4,
    "candidate_pool_size": 30,                 // 下载测速候选池数量 (-dn)
    "download_time": 4,                        // 单 IP 下载探活时长 (-dt，秒)
    "httping": false,                          // 默认使用纯 TCPing 测速 (超快且无 TLS 超时问题)
    "httping_code": 200,
    "colo": "",                                // 留空不限地区；若开启 httping 且指定了 include_colo 则自动联动
    "extra_args": []
  },
  "filter": {
    "target_ip_count": 10,                     // 最终写入 DNS 的 IP 数量
    "min_qualified_ips": 3,                    // 最低达标熔断阈值
    "sort_by": "delay",                        // 排序策略: delay / speed / balanced / colo (0%丢包绝对优先)
    "diversify_colo": true,                    // 启用跨地区轮询打散
    "max_per_colo": 0,                         // 首轮单机房最大入选数 (0 为不限制)
    "max_per_region": 0,                       // 首轮单大区最大入选数 (0 为不限制)
    "max_per_subnet": 2,                       // 同一 /24 C段子网最大入选数 (防同一集群连带阻断)
    "strict_colo_cap": false,                  // false: 机房不足时弹性轮询补满; true: 严格限制不超上限
    "strict_subnet_cap": false,                // false: 子网不足时弹性放宽; true: 严格限制不超上限
    "exclude_colo": [],                        // 留空不排除任何机房
    "include_colo": [],                        // 留空不限制任何机房 (根据家宽实际物理路由优选)
    "min_delay_ms": 0.0,
    "max_delay_ms": 250.0,                     // TCPing 平均延迟上限 (建议 200~300ms)
    "max_loss_rate": 0.05,
    "min_speed_mb": 5.0
  },
  "schedule": {
    "interval_hours": 12,
    "random_delay_minutes": 0
  },
  "notify": {
    "enabled": true,                                   // 置 false 可整体关闭推送 (留空 webhook_url 也自动禁用)
    "provider": "feishu",                             // feishu / dingtalk / wecom / generic (留空按域名自动识别)
    "webhook_url": "https://open.feishu.cn/open-apis/bot/v2/hook/YOUR_BOT_TOKEN",
    "secret": "",                                     // 飞书机器人「签名校验」密钥，未开启签名则留空
    "msg_type": "interactive",                        // interactive 卡片 (推荐) 或 text 纯文本
    "events": ["success", "skipped_protection", "error"],  // 需推送的状态白名单
    "notify_on_dry_run": false,                       // Dry Run 是否也推送 (便于调试)
    "timeout": 10,
    "proxy": "",                                     // 留空不走代理；与 cloudflare.proxy 独立
    "max_retries": 1
  },
  "general": {
    "github_proxy": "https://gh-proxy.org/",
    "log_level": "INFO",
    "log_file": "cfst_ddns.log"
  }
}
```

可用事件状态：`success`（已变更并写入）、`no_change`（线上与结果一致）、`dry_run`（试运行）、`skipped_protection`（熔断跳过，保留旧解析）、`error`（任何阶段的失败）。

## Webhook 通知（飞书）

通知属于旁路能力：**发送失败只记 WARNING，不改变任务退出码，不影响 DNS 同步结果**。

### 飞书自定义机器人配置

1. 在飞书群聊 →「群机器人」→「添加机器人」→「自定义机器人」，复制 Webhook 地址（形如 `https://open.feishu.cn/open-apis/bot/v2/hook/xxxxxxxx`）。
2. 若在安全设置中勾选了「签名校验」，把密钥填入 `notify.secret`（程序会自动计算 `timestamp` 与 `sign`）。
3. 将地址写入 `notify.webhook_url`，然后验证：

```bash
uv run --project .custom/uv-py cfst-ddns --test-notify
```

4. systemd 定时任务无需改动，下一轮执行结束时即自动推送（默认卡片含状态、域名、目标 IP、新增/删除/保持数、耗时、主机与时间）。

### 环境变量覆盖（适合容器 / CI 部署，避免明文写盘）

| 变量 | 作用 |
| :--- | :--- |
| `CFST_WEBHOOK_URL` / `FEISHU_WEBHOOK_URL` | 覆盖 `notify.webhook_url` 并自动启用 |
| `CFST_WEBHOOK_SECRET` / `FEISHU_WEBHOOK_SECRET` | 覆盖 `notify.secret` |

### 其他平台

`provider` 设为 `dingtalk`（钉钉 markdown）或 `wecom`（企业微信 markdown）即可复用同一套消息渲染；任意自建服务使用 `generic`，会 POST 结构化 JSON（`title` / `status` / `text` / `target_ips` / `domains` / `host` / `timestamp`）。

## 命令行参数

| 参数 | 说明 |
| :--- | :--- |
| `-c`, `--config <path>` | 配置文件路径（默认 `.custom/config.json`） |
| `--verify-only` | 仅校验 Cloudflare API Token 与线上解析记录 |
| `--dry-run` | 试运行：执行测速与筛选，仅打印拟变更记录 |
| `--skip-speedtest` | 跳过测速，直接读取现有 CSV 同步（可配合 `--csv <path>`） |
| `--daemon` | 常驻守护模式，按 `schedule.interval_hours` 循环执行 |
| `--test-notify` | 仅发送一条 Webhook 测试消息后退出 |
| `--no-notify` | 本次运行不推送通知（手动调试 / 临时静默） |
| `-v`, `--verbose` | 输出 DEBUG 级别日志 |
