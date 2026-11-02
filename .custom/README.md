# CloudflareSpeedTest 优选 IP 自动同步至 Cloudflare DNS (.custom 专用模块)

本项目为 `CloudflareSpeedTest` 的 Python 自动化扩展模块，专为在 **Debian / Linux VPS / N100** 等单机常驻环境运行设计。

通过该模块，你可以将 CloudflareSpeedTest 筛选出的优质 Anycast/CDN IP 自动同步为 Cloudflare 泛解析记录（如 `*.cf.example.com` 与 `cf.example.com`），构建属于你自己的高可用优选 IP 池。

---

## 核心特性

- **纯标准库实现（Zero-Dependencies）**：基于 Python 3.8+ 标准库（`urllib.request` / `json` / `csv` 等），在 Debian 12 / Ubuntu 等系统中无需 `pip install`，无 PEP 668 环境限制，即开即用。
- **支持泛解析与多 IP 池**：自动管理 `*.cf.example.com` 与 `cf.example.com` 对应的一组 A 记录（如 Top 5 / 10 / 20），实现 DNS 客户端轮询负载。
- **四大安全防护机制**：
  1. **最低有效 IP 熔断**：达标合格 IP 数量不足时（如少于 3 个），放弃更新，保留线上记录。
  2. **预检探活机制**：每次更新前先校验 Cloudflare API Token 权限与 Zone 状态，异常时不进行后续写操作。
  3. **先增后删（Add-before-Delete）**：先成功写入新 IP，再删除过期旧 IP，确保解析池始终非空、平滑过渡无中断。
  4. **异常状态保持**：若测速过程超时、结果为空或网络中断，保留上一轮全部 DNS 记录。
- **无缝对接 Systemd Timer**：提供开箱即用的 Systemd Service 与 Timer 配置，默认每 6 小时自动执行，开机自启。

---

## 模块结构

```text
.custom/
├── __init__.py
├── cloudflare.py          # Cloudflare API v4 客户端封装 (标准库)
├── filter.py              # CSV 结果解析、IP 合规校验、双重阈值过滤与排序
├── speedtest.py           # CloudflareSpeedTest 二进制调用封装与参数装配
├── sync.py                # 核心同步引擎 (Diff 计算、4 重安全熔断、先增后删)
├── main.py                # 命令行主入口程序
├── config.json.example    # 完整配置参数模板
├── config.json            # 生产配置文件 (被 .gitignore 保护，避免泄露秘钥)
├── test_suite.py          # 单元与集成测试套件
├── deploy.sh              # Debian / Linux 一键部署与安装脚本
├── systemd/
│   ├── cfst-ddns.service  # systemd 服务定义
│   └── cfst-ddns.timer    # systemd 6 小时定时触发器
└── README.md              # 模块说明文档
```

---

## 快速部署指南 (Debian / Linux)

### 1. 准备工作：创建 Cloudflare API Token

1. 登录 [Cloudflare 控制台](https://dash.cloudflare.com/profile/api-tokens)。
2. 点击 **“创建令牌”** (Create Token) -> 选择 **“编辑区域 DNS”** (Edit zone DNS) 模板（或自定义令牌）。
3. 权限设置：
   - `区域 (Zone)` - `DNS` - `编辑 (Edit)`
4. 区域资源筛选：
   - 包含 - `特定区域` - 选择你的域名（例如 `example.com`）。
5. 创建令牌并妥善复制保存。

### 2. 初始化配置

在 `.custom` 目录下复制配置模板：

```bash
cp .custom/config.json.example .custom/config.json
nano .custom/config.json
```

编辑关键参数：
- `api_token`: 填入你的 Cloudflare API 令牌。
- `zone_name`: 填入主域名，例如 `"example.com"`。
- `record_names`: 填入要更新的解析记录列表，例如 `["*.cf.example.com", "cf.example.com"]`。
- `target_ip_count`: 保留的优选 IP 数量，默认 `5`（可根据需求改为 `10` 或 `20`）。

### 3. 一键环境部署

运行部署脚本，脚本会自动检测架构、补全 `cfst` 测速二进制文件，并设置权限：

```bash
bash .custom/deploy.sh
```

### 4. 诊断与试运行

**步骤 1：仅校验 Cloudflare API 连通性与现有 DNS 记录**：
```bash
python3 .custom/main.py --verify-only
```
如果输出 `Cloudflare API Token 验证成功` 并列出已有 DNS 记录，说明配置完全正确。

**步骤 2：试运行（完整测速，但仅在控制台展示拟变更的 IP，不实际写入 Cloudflare）**：
```bash
python3 .custom/main.py --dry-run
```

**步骤 3：正式手动执行一次**：
```bash
python3 .custom/main.py
```

### 5. 安装 Systemd 定时任务（每 6 小时自动运行）

使用 root 或 sudo 运行：

```bash
sudo bash .custom/deploy.sh --install-systemd
```

安装完成后常用命令：
```bash
# 查看定时器下次触发时间
systemctl list-timers --all | grep cfst

# 查看服务执行日志
journalctl -u cfst-ddns.service -f

# 立即手动触发一次执行
systemctl start cfst-ddns.service
```

### 6. 卸载 Systemd 定时任务

如需停止并彻底移除系统定时任务与服务：

```bash
sudo bash .custom/deploy.sh --uninstall-systemd
```

---

## 配置文件详解 (`config.json`)

```json
{
  "cloudflare": {
    "api_token": "YOUR_CLOUDFLARE_API_TOKEN", // Cloudflare API 令牌 (DNS Edit 权限)
    "zone_name": "example.com",                // 根域名
    "zone_id": "",                             // 可选，留空时程序会自动通过 zone_name 查询
    "record_names": [                         // 目标解析名称列表 (同时同步泛解析与根前缀)
      "*.cf.example.com",
      "cf.example.com"
    ],
    "record_type": "A",                        // A (IPv4) 或 AAAA (IPv6)
    "ttl": 60,                                 // DNS TTL (秒)
    "proxied": false,                          // 必须为 false (DNS Only)
    "comment": "Auto-updated by CloudflareSpeedTest"
  },
  "speedtest": {
    "binary_path": "cfst",                     // 测速程序文件名或路径
    "ip_file": "ip.txt",                       // IP 段库文件 (IPv4: ip.txt, IPv6: ipv6.txt)
    "specified_ips": [],                       // 可选: 指定测速 IP 或 CIDR 列表 (如 ["104.16.0.0/16"])，留空使用 ip_file
    "test_url": "https://cf.xiu2.xyz/url",     // 测速目标下载文件 URL
    "port": 443,                               // 测速端口 (常用 443 / 80 / 8443 / 2053 等)
    "thread_count": 200,                       // 延迟测速并发线程 (-n)
    "ping_times": 4,                           // 单 IP 延迟测速次数 (-t)
    "candidate_pool_size": 15,                 // 【测速候选池深度】下载测速 IP 数量 (-dn，建议设为 target_ip_count 的 2~3 倍)
    "download_time": 10,                       // 下载测速时间 (-dt，秒)
    "httping": false,                          // 是否切换为 HTTPing 延迟测速模式
    "httping_code": 200,                       // HTTPing 有效响应状态码
    "colo": "",                                // 指定地区三字码 (例如 HKG,SJC,LAX)
    "extra_args": []                           // 传递给 cfst 的额外自定义原始参数
  },
  "filter": {
    "target_ip_count": 5,                      // 【最终 IP 池大小】最终写入 DNS 的优选 IP 数量 (Top N，如 5, 10, 20)
    "min_qualified_ips": 3,                    // 【安全熔断阈值】合格 IP 少于该值时放弃更新，保持上一轮 DNS
    "sort_by": "speed",                        // 【优选排序偏好】"speed"(下载速度优先) / "delay"(延迟优先) / "balanced"(综合平衡)
    "min_delay_ms": 0.0,                       // 平均延迟下限 (-tll，可过滤内网或假延迟)
    "max_delay_ms": 200.0,                     // 平均延迟上限 (毫秒)
    "max_loss_rate": 0.05,                     // 丢包率上限 (0.05 即 5%)
    "min_speed_mb": 5.0                        // 下载速度下限 (MB/s)
  },
  "schedule": {
    "interval_hours": 6,                       // 【定时检测周期】每隔多少小时自动测速并同步一次 (如 3, 6, 12, 24)
    "random_delay_minutes": 0                  // 随机抖动延迟 (分钟)，防止集群整点冲击
  },
  "general": {
    "github_proxy": "https://gh-proxy.org/",   // GitHub Release 下载加速代理 (国内 VPS 默认推荐，可换为其他镜像或留空直连)
    "log_level": "INFO",                       // 日志级别: DEBUG / INFO / WARNING / ERROR
    "log_file": "cfst_ddns.log"                // 日志保存文件
  }
}
```

---

## 命令行参数一览

| 参数 | 说明 |
| :--- | :--- |
| `-c`, `--config <path>` | 指定配置文件路径 (默认 `.custom/config.json`) |
| `--dry-run` | 试运行模式，测速并输出拟执行的 DNS 增删动作，不产生实际修改 |
| `--skip-speedtest` | 跳过执行测速过程，直接使用已有的 CSV 文件做同步 |
| `--csv <path>` | 指定用于分析的 CSV 结果路径（常配合 `--skip-speedtest`） |
| `--verify-only` | 仅检查 Cloudflare API Token 权限与当前域名记录，不测速也不更新 |
| `--daemon` | **常驻守护进程模式**，按照 `schedule.interval_hours` 循环定时执行 (适用于 Docker 或无 systemd 环境) |
| `-v`, `--verbose` | 输出详细 DEBUG 日志 |
