#!/usr/bin/env python3
"""Main Entry Point for CloudflareSpeedTest DDNS Automation."""

import argparse
import json
import logging
import os
import signal
import sys
import time
from pathlib import Path
from typing import Any, Dict

# Package & directory layout:
# .custom/uv-py/src/cfst_ddns/main.py
package_dir = Path(__file__).resolve().parent
src_dir = package_dir.parent
uv_dir = src_dir.parent
custom_dir = uv_dir.parent

if str(src_dir) not in sys.path:
    sys.path.insert(0, str(src_dir))

try:
    from .cloudflare import CloudflareAPIError, CloudflareClient
    from .filter import filter_and_rank_ips, parse_csv
    from .notify import CycleReport, WebhookNotifier
    from .speedtest import SpeedTestError, run_speedtest
    from .sync import DNSUpdater
except (ImportError, ValueError):
    from cfst_ddns.cloudflare import CloudflareAPIError, CloudflareClient
    from cfst_ddns.filter import filter_and_rank_ips, parse_csv
    from cfst_ddns.notify import CycleReport, WebhookNotifier
    from cfst_ddns.speedtest import SpeedTestError, run_speedtest
    from cfst_ddns.sync import DNSUpdater

logger = logging.getLogger("cfst_ddns")


def setup_logging(general_config: Dict[str, Any], verbose: bool = False) -> None:
    """Configure console and file logging."""
    log_level_name = "DEBUG" if verbose else general_config.get("log_level", "INFO").upper()
    level = getattr(logging, log_level_name, logging.INFO)

    formatter = logging.Formatter(
        fmt="[%(asctime)s] [%(levelname)s] [%(name)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    handlers = []

    # Console handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    console_handler.setLevel(level)
    handlers.append(console_handler)

    # File handler
    log_file = general_config.get("log_file", "").strip()
    if log_file:
        log_path = Path(log_file)
        if not log_path.is_absolute():
            log_path = custom_dir / log_path
        log_path.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(str(log_path), encoding="utf-8")
        file_handler.setFormatter(formatter)
        file_handler.setLevel(level)
        handlers.append(file_handler)

    logging.basicConfig(level=level, handlers=handlers)


def load_config(config_path: Path) -> Dict[str, Any]:
    """Load and validate JSON configuration with environment variable fallbacks."""
    if not config_path.exists():
        alt_path = uv_dir / "config.json"
        if config_path == custom_dir / "config.json" and alt_path.exists():
            config_path = alt_path
        else:
            example_path = custom_dir / "config.json.example"
            raise FileNotFoundError(
                f"配置文件不存在: {config_path}\n"
                f"请复制示例文件创建配置文件:\n"
                f"  cp {example_path} {config_path}\n"
                f"并填写其中的 Cloudflare API Token 和解析域名信息。"
            )

    try:
        with open(config_path, mode="r", encoding="utf-8") as f:
            config = json.load(f)
    except json.JSONDecodeError as e:
        raise ValueError(f"配置文件 JSON 格式错误 [{config_path}]: {e}")

    # Environment variable overrides
    cf = config.setdefault("cloudflare", {})
    if "CF_API_TOKEN" in os.environ:
        cf["api_token"] = os.environ["CF_API_TOKEN"]
    elif "CLOUDFLARE_API_TOKEN" in os.environ:
        cf["api_token"] = os.environ["CLOUDFLARE_API_TOKEN"]

    if "CF_ZONE_NAME" in os.environ:
        cf["zone_name"] = os.environ["CF_ZONE_NAME"]
    if "CF_RECORD_NAMES" in os.environ:
        cf["record_names"] = [s.strip() for s in os.environ["CF_RECORD_NAMES"].split(",") if s.strip()]
    elif "CF_RECORD_NAME" in os.environ:
        cf["record_names"] = [s.strip() for s in os.environ["CF_RECORD_NAME"].split(",") if s.strip()]

    # Webhook notification overrides (Feishu webhook URL / 签名密钥)
    notify = config.setdefault("notify", {})
    for env_key in ("CFST_WEBHOOK_URL", "FEISHU_WEBHOOK_URL"):
        if os.environ.get(env_key):
            notify["webhook_url"] = os.environ[env_key].strip()
            notify.setdefault("enabled", True)
            break
    for env_key in ("CFST_WEBHOOK_SECRET", "FEISHU_WEBHOOK_SECRET"):
        if os.environ.get(env_key):
            notify["secret"] = os.environ[env_key].strip()
            break

    return config


def execute_cycle(config: Dict[str, Any], args: argparse.Namespace) -> int:
    """Execute a single cycle and push a webhook summary when it finishes."""
    report = CycleReport(dry_run=getattr(args, "dry_run", False))
    notifier = WebhookNotifier.from_config(config.get("notify", {}))

    try:
        exit_code = _execute_cycle_core(config, args, report)
    except Exception as e:  # 兜底：任何未捕获异常也要能推送失败通知
        logger.error("任务执行发生未预期异常: %s", e, exc_info=getattr(args, "verbose", False))
        report.status = "error"
        report.error = f"{type(e).__name__}: {e}"
        exit_code = 1
    finally:
        report.duration = time.time() - report.started_at
        # --verify-only 属于交互式诊断，无需推送；--no-notify 显式关闭
        if getattr(args, "verify_only", False) or getattr(args, "no_notify", False):
            logger.debug("[通知] 当前模式跳过 Webhook 推送。")
        else:
            try:
                notifier.notify_cycle(report)
            except Exception as e:  # 通知失败绝不能影响主流程
                logger.warning("[通知] 推送过程中发生异常，已忽略: %s", e)

    return exit_code


def _execute_cycle_core(config: Dict[str, Any], args: argparse.Namespace, report: CycleReport) -> int:
    """Run speedtest + filtering + DNS sync, filling the report as it progresses."""
    cf_config = config.get("cloudflare", {})
    speedtest_config = config.get("speedtest", {})
    filter_config = config.get("filter", {})

    logger.info("=== CloudflareSpeedTest DDNS 同步任务开始 ===")

    # Initialize Cloudflare API client
    try:
        client = CloudflareClient(
            api_token=cf_config.get("api_token", ""),
            timeout=int(cf_config.get("timeout", 15)),
            proxy=cf_config.get("proxy", "").strip() or None,
        )
        updater = DNSUpdater(client, cf_config, filter_config)
    except Exception as e:
        logger.error("初始化 Cloudflare 客户端失败: %s", e)
        report.status = "error"
        report.error = f"初始化 Cloudflare 客户端失败: {e}"
        return 1

    # Verify only mode
    if args.verify_only:
        report.status = "verify"
        logger.info("正在执行环境与权限诊断...")
        try:
            zone_id = updater.preflight_check()
            logger.info("诊断通过！当前线上已有记录:")
            for rec_name in updater.record_names:
                records = client.list_records(
                    zone_id=zone_id,
                    record_name=rec_name,
                    record_type=cf_config.get("record_type", "A"),
                )
                logger.info("  域名 [%s] 共有 %d 条解析:", rec_name, len(records))
                for r in records:
                    logger.info(
                        "    - %s -> %s (ID: %s, TTL: %s, Proxied: %s)",
                        r["name"],
                        r["content"],
                        r["id"],
                        r["ttl"],
                        r["proxied"],
                    )
            return 0
        except Exception as e:
            logger.error("诊断发现问题: %s", e)
            return 1

    # Determine CSV file path
    default_csv = custom_dir / "result.csv"
    csv_file = args.csv or default_csv

    # Run SpeedTest or skip
    if not args.skip_speedtest:
        try:
            run_speedtest(
                speedtest_config=speedtest_config,
                filter_config=filter_config,
                output_csv=csv_file,
                base_dir=custom_dir,
            )
        except SpeedTestError as e:
            logger.error("[测速失败] %s", e)
            logger.warning("[防护规则触发] 保留上一轮 DNS 解析，退出更新。")
            report.status = "skipped_protection"
            report.message = f"测速阶段失败，已保留上一轮 DNS 解析: {e}"
            return 0  # Exit cleanly to not break timers
        except Exception as e:
            logger.error("执行测速时发生未预期异常: %s", e, exc_info=args.verbose)
            report.status = "error"
            report.error = f"测速阶段异常: {e}"
            return 1
    else:
        logger.info("已跳过测速环节，使用指定数据源: %s", csv_file)

    # Parse & Filter CSV
    try:
        candidates = parse_csv(csv_file, record_type=cf_config.get("record_type", "A"))
        selected_ips = filter_and_rank_ips(candidates, filter_config)
    except Exception as e:
        logger.error("解析或筛选测速结果失败: %s", e, exc_info=args.verbose)
        report.status = "error"
        report.error = f"解析或筛选测速结果失败: {e}"
        return 1

    # Sync DNS
    try:
        result = updater.sync(selected_ips, dry_run=args.dry_run)
        logger.info("同步执行状态: %s", result.status)
        report.status = result.status
        report.target_ips = list(result.target_ips)
        report.message = result.message
        report.domains = [
            {
                "domain": d.domain,
                "status": d.status,
                "target_ips": list(d.target_ips),
                "current_ips": list(d.current_ips),
                "added_ips": list(d.added_ips),
                "deleted_ips": list(d.deleted_ips),
                "unchanged_ips": list(d.unchanged_ips),
            }
            for d in result.details
        ]
        logger.info("=== 任务完成 ===")
        return 0
    except CloudflareAPIError as e:
        logger.error("Cloudflare API 交互失败: %s", e)
        report.status = "error"
        report.target_ips = [c.ip for c in selected_ips]
        report.error = f"Cloudflare API 交互失败: {e}"
        return 1
    except Exception as e:
        logger.error("DNS 同步过程发生异常: %s", e, exc_info=args.verbose)
        report.status = "error"
        report.target_ips = [c.ip for c in selected_ips]
        report.error = f"DNS 同步过程发生异常: {e}"
        return 1


def main() -> int:
    parser = argparse.ArgumentParser(
        description="CloudflareSpeedTest 优选 IP 自动同步至 Cloudflare DNS (支持泛域名与多 IP 负载池)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "-c",
        "--config",
        type=Path,
        default=custom_dir / "config.json",
        help="配置文件路径 (默认: .custom/config.json)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="试运行模式: 仅展示拟新增/删除记录，不实际修改 Cloudflare DNS",
    )
    parser.add_argument(
        "--skip-speedtest",
        action="store_true",
        help="跳过测速环节，直接使用既有的 CSV 结果文件更新 DNS",
    )
    parser.add_argument(
        "--csv",
        type=Path,
        default=None,
        help="指定用于解析的 CSV 结果文件路径 (搭配 --skip-speedtest 使用)",
    )
    parser.add_argument(
        "--verify-only",
        action="store_true",
        help="仅校验 Cloudflare API Token 与域名解析现状，随后退出",
    )
    parser.add_argument(
        "--daemon",
        action="store_true",
        help="常驻守护进程模式: 按照 config.json 中的 schedule.interval_hours 循环执行",
    )
    parser.add_argument(
        "--no-notify",
        action="store_true",
        help="本次运行不推送 Webhook 通知 (用于手动调试或临时静默)",
    )
    parser.add_argument(
        "--test-notify",
        action="store_true",
        help="仅发送一条 Webhook 测试消息并退出 (用于验证飞书机器人配置)",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="启用详细 Debug 日志",
    )

    args = parser.parse_args()

    try:
        config = load_config(args.config)
    except Exception as e:
        print(f"[错误] {e}", file=sys.stderr)
        return 1

    general_config = config.get("general", {})
    setup_logging(general_config, verbose=args.verbose)

    if args.test_notify:
        notifier = WebhookNotifier.from_config(config.get("notify", {}))
        if not notifier.webhook_url:
            logger.error("未配置 notify.webhook_url，无法发送测试通知。")
            return 1
        ok = notifier.send_test()
        logger.info("测试通知发送结果: %s", "成功" if ok else "失败")
        return 0 if ok else 1

    if not args.daemon:
        return execute_cycle(config, args)

    # Daemon loop mode
    schedule_config = config.get("schedule", {})
    interval_hours = float(schedule_config.get("interval_hours", 12.0))
    interval_seconds = max(60.0, interval_hours * 3600.0)

    logger.info("进入守护进程常驻模式，执行间隔: %.1f 小时 (%.0f 秒)", interval_hours, interval_seconds)

    stop_requested = False

    def handle_signal(sig, frame):
        nonlocal stop_requested
        logger.info("接收到终止信号 (%s)，准备退出守护进程...", sig)
        stop_requested = True

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    while not stop_requested:
        execute_cycle(config, args)
        if stop_requested:
            break
        logger.info("本次循环完成，将在 %.1f 小时后执行下一次测速更新...", interval_hours)

        # Sleep in small slices to respond promptly to signals
        sleep_elapsed = 0
        while sleep_elapsed < interval_seconds and not stop_requested:
            step = min(5, interval_seconds - sleep_elapsed)
            time.sleep(step)
            sleep_elapsed += step

    logger.info("守护进程已安全退出。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
