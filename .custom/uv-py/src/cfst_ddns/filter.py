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


def get_ip_subnet(ip_str: str) -> str:
    """Return normalized /24 network address for IPv4 or /64 network address for IPv6."""
    try:
        addr = ipaddress.ip_address(ip_str.strip())
        if isinstance(addr, ipaddress.IPv4Address):
            return str(ipaddress.ip_network(f"{addr}/24", strict=False).network_address)
        elif isinstance(addr, ipaddress.IPv6Address):
            return str(ipaddress.ip_network(f"{addr}/64", strict=False).network_address)
        return ip_str
    except ValueError:
        return ip_str


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
    # - max_per_region: max IPs per macro-region in primary pass (default 0; <=0 means unlimited)
    # - max_per_subnet: max IPs per /24 (IPv4) or /64 (IPv6) subnet (default 0; <=0 means unlimited)
    # - strict_colo_cap: if False (default), elastically fill up to target_count via round-robin when regions are scarce
    # - strict_subnet_cap: if False (default), elastically relax subnet limits when unique subnets are scarce
    diversify_colo = bool(filter_config.get("diversify_colo", True))
    max_per_colo = int(filter_config.get("max_per_colo", 2))
    max_per_region = int(filter_config.get("max_per_region", 0))
    max_per_subnet = int(filter_config.get("max_per_subnet", 0))
    strict_colo_cap = bool(filter_config.get("strict_colo_cap", False))
    strict_subnet_cap = bool(filter_config.get("strict_subnet_cap", False))

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

    # Candidate pool diagnostic overview
    colo_counter: Dict[str, int] = {}
    delays = [c.delay_ms for c in candidates]
    speeds = [c.download_speed_mb for c in candidates]
    for c in candidates:
        ck = c.colo.strip().upper() if c.colo and c.colo.strip() else "N/A"
        colo_counter[ck] = colo_counter.get(ck, 0) + 1

    sorted_colos = sorted(colo_counter.items(), key=lambda x: -x[1])
    colo_summary = ", ".join(f"{k}:{v}" for k, v in sorted_colos[:8])
    min_cand_delay = min(delays) if delays else 0.0
    max_cand_delay = max(delays) if delays else 0.0
    min_cand_speed = min(speeds) if speeds else 0.0
    max_cand_speed = max(speeds) if speeds else 0.0

    logger.info(
        "候选池原始分布 (共 %d 个): 机房 Top=[%s], 延迟范围=[%.1f ~ %.1f] ms, 速度范围=[%.2f ~ %.2f] MB/s",
        len(candidates),
        colo_summary or "无",
        min_cand_delay,
        max_cand_delay,
        min_cand_speed,
        max_cand_speed,
    )

    if not qualified:
        fail_colo = 0
        fail_delay = 0
        fail_speed = 0
        fail_loss = 0
        for c in candidates:
            ck = c.colo.strip().upper() if c.colo else ""
            if (exclude_colo and ck in exclude_colo) or (include_colo and ck not in include_colo):
                fail_colo += 1
            elif c.loss_rate > max_loss:
                fail_loss += 1
            elif c.delay_ms < min_delay or c.delay_ms > max_delay:
                fail_delay += 1
            elif c.download_speed_mb < min_speed:
                fail_speed += 1

        reasons = []
        if fail_colo > 0:
            reasons.append(f"机房不符限定 (include={list(include_tokens)} / exclude={list(exclude_tokens)}): {fail_colo} 个 (候选机房主要为: {colo_summary})")
        if fail_delay > 0:
            reasons.append(f"延迟超标: {fail_delay} 个 (候选最低延迟为 {min_cand_delay:.1f}ms, 但设定上限为 {max_delay:.1f}ms)")
        if fail_speed > 0:
            reasons.append(f"速度未达标: {fail_speed} 个 (候选最高速度为 {max_cand_speed:.2f}MB/s, 设定下限为 {min_speed:.2f}MB/s)")
        if fail_loss > 0:
            reasons.append(f"丢包率超标: {fail_loss} 个")

        logger.warning("未找到达标 IP！原因诊断分析:\n  * " + "\n  * ".join(reasons))
        if fail_delay > 0 and min_cand_delay > max_delay:
            logger.warning(
                "【建议】检测到最低延迟 (%.1f ms) 高于设定上限 (%.1f ms)！"
                "若使用 HTTPing 模式，因包含完整 HTTPS 握手，建议将 config.json 中的 max_delay_ms 调至 %.0f ms 以上。",
                min_cand_delay,
                max_delay,
                min_cand_delay + 50.0,
            )
        if fail_colo == len(candidates):
            logger.warning(
                "【建议】检测到所有候选 IP 的物理机房 (%s) 均不在限定列表中！"
                "这是 BGP Anycast 物理路由收敛导致的。建议在 config.json 的 include_colo 中加入当前 VPS 实际路由的机房（如 HKG 或 US）。",
                colo_summary,
            )
        return []

    # Multi-tier sorting: 0% packet loss (Tier 0) > <=2% loss (Tier 1) > >2% loss (Tier 2)
    def sort_key(item: IPResult) -> Tuple[Any, ...]:
        loss_tier = 0 if item.loss_rate == 0.0 else (1 if item.loss_rate <= 0.02 else 2)
        if sort_by == "delay":
            return (loss_tier, item.delay_ms, -item.download_speed_mb, item.loss_rate)
        elif sort_by in ("balanced", "colo", "region"):
            score = item.download_speed_mb / max(1.0, item.delay_ms)
            return (loss_tier, -score, item.delay_ms, item.loss_rate)
        else:  # default "speed"
            return (loss_tier, -item.download_speed_mb, item.delay_ms, item.loss_rate)

    if sort_by == "delay":
        logger.info("排序偏好: 0%%丢包优先 + 延迟优先 (0%%丢包梯队 -> 最低延迟 -> 最高速度)")
    elif sort_by in ("balanced", "colo", "region"):
        logger.info("排序偏好: 0%%丢包优先 + 综合平衡评分 (0%%丢包梯队 -> 最高速度/延迟比)")
    else:  # default "speed"
        logger.info("排序偏好: 0%%丢包优先 + 下载速度优先 (0%%丢包梯队 -> 最高速度 -> 最低延迟)")
    qualified.sort(key=sort_key)

    if not diversify_colo:
        selected = []
        subnet_counts: Dict[str, int] = {}
        for item in qualified:
            sub = get_ip_subnet(item.ip)
            if max_per_subnet > 0 and subnet_counts.get(sub, 0) >= max_per_subnet:
                continue
            selected.append(item)
            subnet_counts[sub] = subnet_counts.get(sub, 0) + 1
            if len(selected) >= target_count:
                break
        if len(selected) < target_count and not strict_subnet_cap and max_per_subnet > 0:
            for item in qualified:
                if item not in selected:
                    selected.append(item)
                    if len(selected) >= target_count:
                        break
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
        selected_ips: Set[str] = set()
        colo_counts: Dict[str, int] = {}
        region_counts: Dict[str, int] = {}
        subnet_counts: Dict[str, int] = {}

        max_bucket_size = max(len(b) for b in colo_buckets.values())
        primary_rounds = max_per_colo if max_per_colo > 0 else max_bucket_size

        # Pass 1: Round-robin across colos up to max_per_colo and max_per_region, respecting max_per_subnet
        for _ in range(primary_rounds):
            for colo_key in colo_order:
                if max_per_colo > 0 and colo_counts.get(colo_key, 0) >= max_per_colo:
                    continue
                region_key = get_colo_region(colo_key)
                if max_per_region > 0 and region_counts.get(region_key, 0) >= max_per_region:
                    continue

                bucket = colo_buckets[colo_key]
                for candidate in bucket:
                    if candidate.ip in selected_ips:
                        continue
                    sub = get_ip_subnet(candidate.ip)
                    if max_per_subnet > 0 and subnet_counts.get(sub, 0) >= max_per_subnet:
                        continue
                    selected.append(candidate)
                    selected_ips.add(candidate.ip)
                    colo_counts[colo_key] = colo_counts.get(colo_key, 0) + 1
                    region_counts[region_key] = region_counts.get(region_key, 0) + 1
                    subnet_counts[sub] = subnet_counts.get(sub, 0) + 1
                    break
                if len(selected) >= target_count:
                    break
            if len(selected) >= target_count:
                break

        # Pass 2: Elastic fallback (relax colo & region limits, maintain subnet diversity if possible)
        if len(selected) < target_count and not strict_colo_cap:
            logger.info(
                "初筛抽取 %d 个 IP，尚未凑满目标数 %d，启动跨地区轮询弹性补齐...",
                len(selected),
                target_count,
            )
            for _ in range(max_bucket_size):
                for colo_key in colo_order:
                    bucket = colo_buckets[colo_key]
                    for candidate in bucket:
                        if candidate.ip in selected_ips:
                            continue
                        sub = get_ip_subnet(candidate.ip)
                        if max_per_subnet > 0 and subnet_counts.get(sub, 0) >= max_per_subnet:
                            continue
                        selected.append(candidate)
                        selected_ips.add(candidate.ip)
                        subnet_counts[sub] = subnet_counts.get(sub, 0) + 1
                        break
                    if len(selected) >= target_count:
                        break
                if len(selected) >= target_count:
                    break

        # Pass 3: Final fallback (relax subnet limit if candidates in unique subnets were exhausted)
        if len(selected) < target_count and not strict_subnet_cap and max_per_subnet > 0:
            logger.info("可用独立子网耗尽，放宽同 C 段限制以凑满目标数...")
            for _ in range(max_bucket_size):
                for colo_key in colo_order:
                    bucket = colo_buckets[colo_key]
                    for candidate in bucket:
                        if candidate.ip in selected_ips:
                            continue
                        selected.append(candidate)
                        selected_ips.add(candidate.ip)
                        break
                    if len(selected) >= target_count:
                        break
                if len(selected) >= target_count:
                    break

    # Summarize final selected colos & subnets
    selected_counts: Dict[str, int] = {}
    subnet_counts_summary: Dict[str, int] = {}
    for item in selected:
        ck = item.colo.strip().upper() if item.colo and item.colo.strip() else "N/A"
        selected_counts[ck] = selected_counts.get(ck, 0) + 1
        sub = get_ip_subnet(item.ip)
        subnet_counts_summary[sub] = subnet_counts_summary.get(sub, 0) + 1

    selected_dist = ", ".join(
        f"{c}[{get_colo_region(c)}]:{cnt}" if get_colo_region(c) != c else f"{c}:{cnt}"
        for c, cnt in selected_counts.items()
    )

    logger.info(
        "选定 Top %d 个优选 IP (覆盖 %d 个大区/机房: %s; 覆盖 %d 个独立 C 段):",
        len(selected),
        len(selected_counts),
        selected_dist or "无",
        len(subnet_counts_summary),
    )
    for idx, item in enumerate(selected, start=1):
        logger.info("  [%02d] %s (网段: %s)", idx, item, get_ip_subnet(item.ip))

    return selected
