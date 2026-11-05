# CloudflareSpeedTest 优选 IP 自动同步模块 (.custom)

基于 `uv` 标准结构的 `CloudflareSpeedTest` 自动化扩展模块，自动筛选多地区低延迟 Anycast IP 并同步至 Cloudflare 泛解析（如 `*.cf.example.com`）。

## 核心特性

- **初筛锁区 + 跨地区轮询打散**：通过 `HTTP HEAD` 零流量初筛目标大区（默认 `JP/SG/KR/TW`，排除 `HKG/EU`），再按大区穿插与单机房配额上限（`max_per_colo: 2`）轮询抽取，防止单一机房垄断结果池。
- **轻量探活省流**：仅对初筛锁区后的 30 个目标地区候选 IP 执行 4 秒下载探活，12 小时周期下月均流量仅约 `50~70 GB`。
- **四重安全熔断**：API 预检探活、最低合格 IP 数量熔断（`< min_qualified_ips` 放弃更新）、先增后删（Add-before-Delete 平滑切换）、测速异常保留线上记录。
- **零三方依赖**：基于 Python 标准库实现，支持 `uv run` 与原生 `python3` 直接运行。

## 目录结构

```text
.custom/
├── uv-py/                     # uv Python 项目 (src-layout)
│   ├── pyproject.toml         # 项目元数据与 CLI 入口 (cfst-ddns)
│   ├── uv.lock
│   ├── src/cfst_ddns/         # 核心实现 (cloudflare / filter / speedtest / sync / main)
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
    "thread_count": 200,
    "ping_times": 4,
    "candidate_pool_size": 30,                 // 下载测速候选池数量 (-dn)
    "download_time": 4,                        // 单 IP 下载探活时长 (-dt，秒)
    "httping": true,                           // 开启 HTTP HEAD 测速并提取地区码
    "httping_code": 200,
    "colo": "",                                // 留空且开启 httping 时自动继承 filter.include_colo
    "extra_args": []
  },
  "filter": {
    "target_ip_count": 10,                     // 最终写入 DNS 的 IP 数量
    "min_qualified_ips": 5,                    // 最低达标熔断阈值
    "sort_by": "delay",                        // 排序策略: delay / speed / balanced / colo
    "diversify_colo": true,                    // 启用跨地区轮询打散
    "max_per_colo": 2,                         // 首轮单机房最大入选数
    "strict_colo_cap": false,                  // false: 机房不足时弹性轮询补满; true: 严格限制不超上限
    "exclude_colo": ["HKG", "EU"],             // 排除大区 (HK/MO/EU/US) 或三字码
    "include_colo": ["JP", "SG", "KR", "TW"],  // 限定大区 (JP/SG/KR/TW/US) 或三字码
    "min_delay_ms": 0.0,
    "max_delay_ms": 200.0,
    "max_loss_rate": 0.05,
    "min_speed_mb": 5.0
  },
  "schedule": {
    "interval_hours": 12,
    "random_delay_minutes": 0
  },
  "general": {
    "github_proxy": "https://gh-proxy.org/",
    "log_level": "INFO",
    "log_file": "cfst_ddns.log"
  }
}
```

## 命令行参数

| 参数 | 说明 |
| :--- | :--- |
| `-c`, `--config <path>` | 配置文件路径（默认 `.custom/config.json`） |
| `--verify-only` | 仅校验 Cloudflare API Token 与线上解析记录 |
| `--dry-run` | 试运行：执行测速与筛选，仅打印拟变更记录 |
| `--skip-speedtest` | 跳过测速，直接读取现有 CSV 同步（可配合 `--csv <path>`） |
| `--daemon` | 常驻守护模式，按 `schedule.interval_hours` 循环执行 |
| `-v`, `--verbose` | 输出 DEBUG 级别日志 |
