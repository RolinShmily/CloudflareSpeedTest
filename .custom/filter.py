"""CSV Parsing, IP validation, filtering, and ranking."""

import csv
import ipaddress
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("cfst_ddns.filter")


@dataclass
class IPResult:
    ip: str
    sended: int
    received: int
    loss_rate: float
    delay_ms: float
    download_speed_mb: float
    colo: str = "N/A"

    def __str__(self) -> str:
        return (
            f"{self.ip:<16} | 丢包率: {self.loss_rate*100:4.1f}% | "
            f"延迟: {self.delay_ms:6.2f}ms | 速度: {self.download_speed_mb:5.2f} MB/s | "
            f"地区: {self.colo}"
        )


def is_valid_ip(ip_str: str, record_type: str = "A") -> bool:
    """Validate whether an IP string is a valid IPv4 or IPv6 address."""
    try:
        addr = ipaddress.ip_address(ip_str.strip())
        if record_type.upper() == "A":
            return isinstance(addr, ipaddress.IPv4Address) and not addr.is_private and not addr.is_loopback
        elif record_type.upper() == "AAAA":
            return isinstance(addr, ipaddress.IPv6Address) and not addr.is_private and not addr.is_loopback
        return True
    except ValueError:
        return False


def parse_csv(csv_path: Path, record_type: str = "A") -> List[IPResult]:
    """Parse result.csv produced by CloudflareSpeedTest."""
    if not csv_path.exists():
        logger.warning("测速结果 CSV 文件不存在: %s", csv_path)
        return []

    results: List[IPResult] = []
    with open(csv_path, mode="r", encoding="utf-8-sig", errors="replace") as f:
        reader = csv.reader(f)
        header = next(reader, None)
        if not header:
            logger.warning("CSV 文件为空: %s", csv_path)
            return []

        # Map header columns dynamically if possible
        col_map = {"ip": 0, "sended": 1, "received": 2, "loss": 3, "delay": 4, "speed": 5, "colo": 6}
        for idx, col in enumerate(header):
            col_lower = col.strip().lower()
            if "ip" in col_lower:
                col_map["ip"] = idx
            elif "已发送" in col_lower or "sent" in col_lower:
                col_map["sended"] = idx
            elif "已接收" in col_lower or "recv" in col_lower:
                col_map["received"] = idx
            elif "丢包" in col_lower or "loss" in col_lower:
                col_map["loss"] = idx
            elif "延迟" in col_lower or "delay" in col_lower:
                col_map["delay"] = idx
            elif "速度" in col_lower or "speed" in col_lower:
                col_map["speed"] = idx
            elif "地区" in col_lower or "colo" in col_lower:
                col_map["colo"] = idx

        for row_num, row in enumerate(reader, start=2):
            if not row or len(row) <= max(col_map.values()):
                continue
            try:
                ip = row[col_map["ip"]].strip()
                if not is_valid_ip(ip, record_type):
                    continue

                sended = int(row[col_map["sended"]].strip() or 0)
                received = int(row[col_map["received"]].strip() or 0)
                loss_rate = float(row[col_map["loss"]].strip() or 0.0)
                delay_ms = float(row[col_map["delay"]].strip() or 9999.0)
                speed_mb = float(row[col_map["speed"]].strip() or 0.0)
                colo = row[col_map["colo"]].strip() if col_map["colo"] < len(row) else "N/A"

                results.append(
                    IPResult(
                        ip=ip,
                        sended=sended,
                        received=received,
                        loss_rate=loss_rate,
                        delay_ms=delay_ms,
                        download_speed_mb=speed_mb,
                        colo=colo,
                    )
                )
            except (ValueError, IndexError) as e:
                logger.debug("解析 CSV 第 %d 行失败: %s (%s)", row_num, row, e)
                continue

    logger.info("从 CSV [%s] 中成功解析出 %d 个有效 IP 记录", csv_path.name, len(results))
    return results


def filter_and_rank_ips(candidates: List[IPResult], filter_config: Dict[str, Any]) -> List[IPResult]:
    """Filter candidate IPs against thresholds and select Top N ordered by speed & latency."""
    min_delay = float(filter_config.get("min_delay_ms", 0.0))
    max_delay = float(filter_config.get("max_delay_ms", 200.0))
    max_loss = float(filter_config.get("max_loss_rate", 0.05))
    min_speed = float(filter_config.get("min_speed_mb", 5.0))
    target_count = int(filter_config.get("target_ip_count", 5))
    sort_by = str(filter_config.get("sort_by", "speed")).strip().lower()

    qualified: List[IPResult] = []
    for c in candidates:
        if c.loss_rate > max_loss:
            continue
        if c.delay_ms < min_delay or c.delay_ms > max_delay:
            continue
        if c.download_speed_mb < min_speed:
            continue
        qualified.append(c)

    logger.info(
        "过滤条件: 丢包率 <= %.1f%%, 延迟 [%.0f ~ %.0f] ms, 下载速度 >= %.2f MB/s -> 达标 IP 数量: %d / %d",
        max_loss * 100,
        min_delay,
        max_delay,
        min_speed,
        len(qualified),
        len(candidates),
    )

    # Sort criteria based on user preference
    if sort_by == "delay":
        logger.info("排序偏好: 延迟优先 (最低延迟 -> 最高下载速度 -> 最低丢包)")
        qualified.sort(key=lambda x: (x.delay_ms, -x.download_speed_mb, x.loss_rate))
    elif sort_by == "balanced":
        logger.info("排序偏好: 综合平衡评分")
        # Higher score is better: speed / (delay + 1)
        qualified.sort(key=lambda x: (-(x.download_speed_mb / max(1.0, x.delay_ms)), x.delay_ms, x.loss_rate))
    else:  # default "speed"
        logger.info("排序偏好: 下载速度优先 (最高下载速度 -> 最低延迟 -> 最低丢包)")
        qualified.sort(key=lambda x: (-x.download_speed_mb, x.delay_ms, x.loss_rate))

    selected = qualified[:target_count]
    logger.info("选定 Top %d 个优选 IP (目标池大小):", len(selected))
    for idx, item in enumerate(selected, start=1):
        logger.info("  [%02d] %s", idx, item)

    return selected
