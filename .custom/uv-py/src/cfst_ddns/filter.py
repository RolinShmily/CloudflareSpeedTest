"""CSV Parsing, IP validation, filtering, and ranking."""

import csv
import ipaddress
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple, Union

logger = logging.getLogger("cfst_ddns.filter")

# Cloudflare 常见核心地区与 IATA 机场三字码映射表
REGION_COLO_MAP: Dict[str, List[str]] = {
    "JP": ["NRT", "HND", "KIX", "FUK", "OKA", "NGO", "CTS", "SDJ"],
    "SG": ["SIN", "XSP"],
    "KR": ["ICN", "GMP", "PUS"],
    "TW": ["TPE", "TSA", "KHH", "RMQ"],
    "US": [
        # 美西核心与常见机房
        "SJC", "LAX", "SEA", "SFO", "PDX", "SAN", "SMF", "ONT", "BUR", "OAK",
        "LAS", "PHX", "SLC", "DEN", "ABQ", "HNL", "ANC",
        # 美中常见机房
        "ORD", "DFW", "IAH", "AUS", "SAT", "MCI", "MSP", "STL", "OMA", "OKC",
        "MEM", "BNA", "IND", "CMH", "CLE", "DTW", "MKE", "MSY",
        # 美东常见机房
        "IAD", "DCA", "BWI", "EWR", "JFK", "LGA", "BOS", "PHL", "PIT", "BUF",
        "RDU", "CLT", "RIC", "ORF", "ATL", "MIA", "FLL", "TPA", "MCO", "JAX", "TLH",
    ],
    "HK": ["HKG"],
    "MO": ["MFM"],
    "EU": [
        "FRA", "AMS", "LHR", "CDG", "MAD", "MXP", "ZRH", "VIE", "WAW", "PRG",
        "ARN", "HEL", "OSL", "CPH", "DUB", "BRU", "LIS", "MAN", "EDI", "MUC",
        "DUS", "HAM", "TXL", "BER", "MRS", "LYS", "BCN", "FCO", "ATH", "BUD",
        "OTP", "SOF", "ZAG", "BEG", "GVA", "LUX", "KEF", "TLL", "RIX", "VNO",
        "KBP", "IST",
    ],
}

COLO_TO_REGION: Dict[str, str] = {
    colo: region
    for region, colos in REGION_COLO_MAP.items()
    for colo in colos
}


def get_colo_region(colo: str) -> str:
    """Return the macro-region code (e.g. JP, SG, KR, TW, US) for an IATA colo code."""
    if not colo:
        return "N/A"
    colo_upper = colo.strip().upper()
    return COLO_TO_REGION.get(colo_upper, colo_upper)


def _parse_raw_colo_tokens(raw: Union[str, Iterable[Any], None]) -> List[str]:
    """Parse raw config value (comma-separated string or iterable) into normalized tokens."""
    if not raw:
        return []
    tokens: List[str] = []
    if isinstance(raw, str):
        parts = raw.split(",")
    elif isinstance(raw, Iterable):
        parts = []
        for item in raw:
            if isinstance(item, str):
                parts.extend(item.split(","))
            elif item is not None:
                parts.append(str(item))
    else:
        return []

    for p in parts:
        cleaned = p.strip().upper()
        if cleaned:
            tokens.append(cleaned)
    return tokens


def expand_colo_list(raw: Union[str, Iterable[Any], None]) -> List[str]:
    """Expand region codes (e.g. JP, SG, KR, TW, US) and IATA codes into a deduplicated list of IATA colos."""
    tokens = _parse_raw_colo_tokens(raw)
    expanded: List[str] = []
    seen: Set[str] = set()
    for token in tokens:
        colos = REGION_COLO_MAP.get(token, [token])
        for c in colos:
            if c not in seen:
                seen.add(c)
                expanded.append(c)
    return expanded


def expand_colo_set(raw: Union[str, Iterable[Any], None]) -> Set[str]:
    """Expand region codes and IATA codes into a set of uppercase IATA colos."""
    return set(expand_colo_list(raw))


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


def _build_colo_round_robin_order(
    colo_buckets: Dict[str, List[IPResult]],
    include_tokens: List[str],
) -> List[str]:
    """Build the round-robin visiting order of colo buckets.

    If include_tokens specifies macro-regions/colos (e.g. ["JP", "SG", "KR", "TW", "US"]),
    interleave available colos across those configured groups so a region with many colos
    (like US with LAX/SJC/SEA) does not crowd out single-colo regions (like SG/SIN or KR/ICN).
    """
    available_colos = [c for c in colo_buckets.keys() if c != "N/A"]
    if not include_tokens:
        order = list(available_colos)
        if "N/A" in colo_buckets:
            order.append("N/A")
        return order

    # Group available colos by the user's configured include_tokens order
    group_queues: List[List[str]] = []
    assigned: Set[str] = set()
    for token in include_tokens:
        token_colos = set(REGION_COLO_MAP.get(token, [token]))
        matching = [c for c in available_colos if c in token_colos and c not in assigned]
        if matching:
            group_queues.append(matching)
            assigned.update(matching)

    remaining = [c for c in available_colos if c not in assigned]
    if remaining:
        group_queues.append(remaining)

    # Interleave across group_queues
    order: List[str] = []
    max_len = max((len(q) for q in group_queues), default=0)
    for idx in range(max_len):
        for q in group_queues:
            if idx < len(q):
                order.append(q[idx])

    if "N/A" in colo_buckets:
        order.append("N/A")
    return order


def filter_and_rank_ips(candidates: List[IPResult], filter_config: Dict[str, Any]) -> List[IPResult]:
    """Filter candidate IPs against thresholds and select Top N with regional diversification."""
    min_delay = float(filter_config.get("min_delay_ms", 0.0))
    max_delay = float(filter_config.get("max_delay_ms", 200.0))
    max_loss = float(filter_config.get("max_loss_rate", 0.05))
    min_speed = float(filter_config.get("min_speed_mb", 5.0))
    target_count = int(filter_config.get("target_ip_count", 10))
    sort_by = str(filter_config.get("sort_by", "delay")).strip().lower()

    # Regional diversification controls:
    # - diversify_colo: whether to select across regions in round-robin order (default True)
    # - max_per_colo: max IPs per 3-letter colo in primary pass (default 2; <=0 means unlimited round-robin)
    # - strict_colo_cap: if False (default), elastically fill up to target_count via round-robin when regions are scarce
    diversify_colo = bool(filter_config.get("diversify_colo", True))
    max_per_colo = int(filter_config.get("max_per_colo", 2))
    strict_colo_cap = bool(filter_config.get("strict_colo_cap", False))

    # Colo filtering: supports both macro-regions (JP, SG, KR, TW, US, HK, EU) and 3-letter IATA codes
    exclude_colo_raw = filter_config.get("exclude_colo", [])
    exclude_tokens = _parse_raw_colo_tokens(exclude_colo_raw)
    exclude_colo = expand_colo_set(exclude_colo_raw)

    include_colo_raw = filter_config.get("include_colo", [])
    include_tokens = _parse_raw_colo_tokens(include_colo_raw)
    include_colo = expand_colo_set(include_colo_raw)

    if exclude_colo or include_colo:
        has_colo = any(c.colo and c.colo.strip() and c.colo.strip().upper() != "N/A" for c in candidates)
        if candidates and not has_colo:
            logger.warning(
                "配置了 exclude_colo / include_colo，但解析到的 IP 均无有效地区码 (N/A)！"
                "提示: 请确保 test_url 指向 Cloudflare 节点或将 speedtest.httping 设为 true。"
            )

    qualified: List[IPResult] = []
    for c in candidates:
        colo_upper = c.colo.strip().upper() if c.colo else ""
        if exclude_colo and colo_upper in exclude_colo:
            continue
        if include_colo and colo_upper not in include_colo:
            continue
        if c.loss_rate > max_loss:
            continue
        if c.delay_ms < min_delay or c.delay_ms > max_delay:
            continue
        if c.download_speed_mb < min_speed:
            continue
        qualified.append(c)

    filter_desc = [
        f"丢包率 <= {max_loss * 100:.1f}%",
        f"延迟 [{min_delay:.0f} ~ {max_delay:.0f}] ms",
        f"下载速度 >= {min_speed:.2f} MB/s",
    ]
    if exclude_tokens:
        filter_desc.append(f"排除地区: {','.join(exclude_tokens)}")
    if include_tokens:
        filter_desc.append(f"限定地区: {','.join(include_tokens)}")

    logger.info(
        "过滤条件: %s -> 达标 IP 数量: %d / %d",
        ", ".join(filter_desc),
        len(qualified),
        len(candidates),
    )

    # Sort criteria within each region (or globally if diversify_colo is False)
    if sort_by == "delay":
        logger.info("排序偏好: 地区打散 + 延迟优先 (同地区内最低延迟 -> 最高速度 -> 最低丢包)")
        qualified.sort(key=lambda x: (x.delay_ms, -x.download_speed_mb, x.loss_rate))
    elif sort_by in ("balanced", "colo", "region"):
        logger.info("排序偏好: 地区优先打散 + 同地区综合平衡评分")
        qualified.sort(key=lambda x: (-(x.download_speed_mb / max(1.0, x.delay_ms)), x.delay_ms, x.loss_rate))
    else:  # default "speed"
        logger.info("排序偏好: 地区打散 + 下载速度优先 (同地区内最高速度 -> 最低延迟 -> 最低丢包)")
        qualified.sort(key=lambda x: (-x.download_speed_mb, x.delay_ms, x.loss_rate))

    if not qualified:
        return []

    if not diversify_colo:
        selected = qualified[:target_count]
    else:
        # Group qualified IPs by normalized colo code
        colo_buckets: Dict[str, List[IPResult]] = {}
        for item in qualified:
            colo_key = item.colo.strip().upper() if item.colo and item.colo.strip() else "N/A"
            colo_buckets.setdefault(colo_key, []).append(item)

        colo_order = _build_colo_round_robin_order(colo_buckets, include_tokens)
        dist_summary = ", ".join(
            f"{c}[{get_colo_region(c)}]({len(colo_buckets[c])})" if get_colo_region(c) != c else f"{c}({len(colo_buckets[c])})"
            for c in colo_order
        )
        logger.info("候选池达标地区分布 (%d 个机房): %s", len(colo_order), dist_summary)

        selected: List[IPResult] = []
        max_bucket_size = max(len(b) for b in colo_buckets.values())
        primary_rounds = max_per_colo if max_per_colo > 0 else max_bucket_size

        # Pass 1: Round-robin across colos up to max_per_colo
        for round_idx in range(min(primary_rounds, max_bucket_size)):
            for colo_key in colo_order:
                bucket = colo_buckets[colo_key]
                if round_idx < len(bucket):
                    selected.append(bucket[round_idx])
                    if len(selected) >= target_count:
                        break
            if len(selected) >= target_count:
                break

        # Pass 2: Elastic fallback (continue round-robin if regions were too few to reach target_count)
        if len(selected) < target_count and not strict_colo_cap and max_per_colo > 0 and max_bucket_size > primary_rounds:
            logger.info(
                "按单机房上限 (%d 个) 轮询抽取了 %d 个 IP，尚未凑满目标数 %d，启动跨地区轮询弹性补齐...",
                max_per_colo,
                len(selected),
                target_count,
            )
            for round_idx in range(primary_rounds, max_bucket_size):
                for colo_key in colo_order:
                    bucket = colo_buckets[colo_key]
                    if round_idx < len(bucket):
                        selected.append(bucket[round_idx])
                        if len(selected) >= target_count:
                            break
                if len(selected) >= target_count:
                    break

    # Summarize final selected colos
    selected_counts: Dict[str, int] = {}
    for item in selected:
        ck = item.colo.strip().upper() if item.colo and item.colo.strip() else "N/A"
        selected_counts[ck] = selected_counts.get(ck, 0) + 1
    selected_dist = ", ".join(
        f"{c}[{get_colo_region(c)}]:{cnt}" if get_colo_region(c) != c else f"{c}:{cnt}"
        for c, cnt in selected_counts.items()
    )

    logger.info("选定 Top %d 个优选 IP (地区分布: %s):", len(selected), selected_dist or "无")
    for idx, item in enumerate(selected, start=1):
        logger.info("  [%02d] %s", idx, item)

    return selected
