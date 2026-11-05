"""Runner for the CloudflareSpeedTest Go binary."""

import logging
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    from .filter import expand_colo_list, expand_colo_set
except (ImportError, ValueError):
    from filter import expand_colo_list, expand_colo_set

logger = logging.getLogger("cfst_ddns.speedtest")


class SpeedTestError(Exception):
    """Raised when CloudflareSpeedTest fails to execute or output is invalid."""
    pass


def find_binary(configured_path: str = "cfst", base_dir: Optional[Path] = None) -> Path:
    """Locate the CloudflareSpeedTest executable."""
    candidates = []
    if configured_path:
        candidates.append(Path(configured_path))

    if base_dir is None:
        base_dir = Path(__file__).resolve().parent

    # Check .custom folder and project root
    for name in ["cfst", "CloudflareSpeedTest", "cfst.exe", "CloudflareSpeedTest.exe"]:
        candidates.append(base_dir / name)
        candidates.append(base_dir.parent / name)
        # Check subdirectories (e.g. cfst_linux_amd64/cfst)
        for sub in base_dir.parent.glob("cfst_*"):
            if sub.is_dir():
                candidates.append(sub / name)

    for cand in candidates:
        if cand.is_file() and os.access(cand, os.X_OK if sys.platform != "win32" else os.R_OK):
            return cand.resolve()

    # Check system PATH
    for name in ["cfst", "CloudflareSpeedTest"]:
        found = shutil.which(name)
        if found:
            return Path(found).resolve()

    raise FileNotFoundError(
        f"未找到 CloudflareSpeedTest 可执行程序！\n"
        f"请确保以下路径之一存在并具有执行权限：\n"
        f"  1. 配置文件中的 'binary_path'\n"
        f"  2. {base_dir / 'cfst'} (或主项目目录下的 cfst)\n"
        f"  3. 系统 PATH 路径中 (可直接通过命令运行 cfst)\n"
        f"提示: 在 Linux 上可运行 'chmod +x cfst' 赋予执行权限。"
    )


def find_ip_file(configured_ip_file: str, base_dir: Optional[Path] = None) -> Path:
    """Locate the IP range file (e.g. ip.txt or ipv6.txt)."""
    if base_dir is None:
        base_dir = Path(__file__).resolve().parent

    candidates = [
        Path(configured_ip_file),
        base_dir / configured_ip_file,
        base_dir.parent / configured_ip_file,
        base_dir.parent / "ip.txt",
    ]
    for cand in candidates:
        if cand.is_file():
            return cand.resolve()

    raise FileNotFoundError(f"未找到本地 IP 测速数据文件: {configured_ip_file}，请确保仓库根目录存在 ip.txt。")


def run_speedtest(
    speedtest_config: Dict[str, Any],
    filter_config: Dict[str, Any],
    output_csv: Path,
    base_dir: Optional[Path] = None,
) -> Path:
    """Execute CloudflareSpeedTest and wait for output.csv."""
    bin_path = find_binary(speedtest_config.get("binary_path", "cfst"), base_dir=base_dir)
    ip_path = find_ip_file(speedtest_config.get("ip_file", "ip.txt"), base_dir=base_dir)

    # Ensure parent dir of output_csv exists
    output_csv.parent.mkdir(parents=True, exist_ok=True)
    if output_csv.exists():
        output_csv.unlink()

    cmd = [
        str(bin_path),
        "-f", str(ip_path),
        "-o", str(output_csv),
        "-p", "0",  # Don't hold on terminal output
    ]

    # Threads
    threads = speedtest_config.get("thread_count", 200)
    if threads:
        cmd.extend(["-n", str(threads)])

    # Ping times
    ping_times = speedtest_config.get("ping_times", 4)
    if ping_times:
        cmd.extend(["-t", str(ping_times)])

    # Download test candidate pool count & duration
    target_count = int(filter_config.get("target_ip_count", 10))
    dn = speedtest_config.get("candidate_pool_size") or speedtest_config.get("download_count")
    if dn is not None:
        dn = int(dn)
        if dn < target_count:
            logger.warning(
                "配置的候选池测速量 (-dn %d) 小于目标保留 IP 数 (target_ip_count %d)，自动上调为 %d 以确保充足优选空间",
                dn,
                target_count,
                max(target_count * 2, 30),
            )
            dn = max(target_count * 2, 30)
    else:
        # Default candidate pool is 30 (3x of default target_count 10)
        dn = max(target_count * 3, 30)

    cmd.extend(["-dn", str(dn)])

    dt = speedtest_config.get("download_time", 4)
    if dt:
        cmd.extend(["-dt", str(dt)])

    # Port
    port = speedtest_config.get("port", 443)
    if port:
        cmd.extend(["-tp", str(port)])

    # Note: Do NOT pass -tl (max delay), -tll, -tlr (max loss), or -sl (min speed) to cfst binary!
    # If network latency/loss/speed doesn't meet cfst's hard CLI criteria, cfst empties the IP set
    # and aborts CSV export entirely (producing 0 results).
    # Instead, cfst should simply measure and return the Top candidate IPs with their actual measurements,
    # and filter.py in Python will cleanly evaluate thresholds with full safety fuses and reporting.

    # Always enable cfst debug flag so it prints detailed diagnostics
    cmd.append("-debug")

    # HTTPing mode
    if speedtest_config.get("httping", False):
        cmd.append("-httping")

    # HTTPing valid response code
    httping_code = speedtest_config.get("httping_code")
    if httping_code:
        cmd.extend(["-httping-code", str(httping_code)])

    # Custom test URL
    test_url = speedtest_config.get("test_url", "").strip()
    if test_url:
        cmd.extend(["-url", test_url])

    # Specific colo: ONLY pass -cfcolo if explicitly configured in speedtest.colo!
    # Do NOT silently inherit filter.include_colo, because if the VPS network BGP Anycast
    # routes to HKG or US, forcing -cfcolo NRT,SIN... causes cfst to drop 100% of IPs during ping,
    # producing 0 results. filter.py will handle regional selection and reporting cleanly.
    raw_colo = speedtest_config.get("colo", "").strip()
    if raw_colo:
        expanded_colos = expand_colo_list(raw_colo)
        if expanded_colos:
            excluded = expand_colo_set(filter_config.get("exclude_colo", []))
            if excluded:
                expanded_colos = [c for c in expanded_colos if c not in excluded]
            if expanded_colos:
                if "-httping" not in cmd:
                    logger.info("已显式指定测速地区 (speedtest.colo)，自动启用 -httping 模式以使 -cfcolo 生效")
                    cmd.append("-httping")
                cmd.extend(["-cfcolo", ",".join(expanded_colos)])

    # Specified IP or CIDR ranges (-ip)
    specified_ips = speedtest_config.get("specified_ips")
    if specified_ips:
        if isinstance(specified_ips, list):
            cmd.extend(["-ip", ",".join(str(i).strip() for i in specified_ips if str(i).strip())])
        elif isinstance(specified_ips, str) and specified_ips.strip():
            cmd.extend(["-ip", specified_ips.strip()])

    # Extra arguments
    extra = speedtest_config.get("extra_args", [])
    if isinstance(extra, list):
        cmd.extend(extra)

    cmd_str = " ".join(cmd)
    logger.info("启动 CloudflareSpeedTest 测速进程...")
    logger.debug("执行命令: %s", cmd_str)

    try:
        proc = subprocess.run(
            cmd,
            cwd=str(bin_path.parent),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if proc.returncode != 0:
            logger.warning("CloudflareSpeedTest 进程退出码为 %d\n输出:\n%s", proc.returncode, proc.stdout)
        else:
            logger.debug("CloudflareSpeedTest 输出:\n%s", proc.stdout)
    except Exception as e:
        raise SpeedTestError(f"执行 CloudflareSpeedTest 发生异常: {e}")

    if not output_csv.exists() or output_csv.stat().st_size == 0:
        if proc.stdout:
            logger.error("CloudflareSpeedTest 控制台输出内容:\n%s", proc.stdout.strip())
        raise SpeedTestError(
            f"CloudflareSpeedTest 未能生成有效的结果文件: {output_csv}！\n"
            f"可能原因: 网络测速全部超时/丢包，或测速参数限制过严未能凑齐 IP。"
        )

    logger.info("CloudflareSpeedTest 测速完成，结果写入: %s", output_csv)
    return output_csv
