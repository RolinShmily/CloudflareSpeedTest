"""Core DNS Synchronization Engine with 4-layer Safety Protections."""

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Set, Union

try:
    from .cloudflare import CloudflareAPIError, CloudflareClient
    from .filter import IPResult
except (ImportError, ValueError):
    from cloudflare import CloudflareAPIError, CloudflareClient
    from filter import IPResult

logger = logging.getLogger("cfst_ddns.sync")


@dataclass
class DomainSyncDetail:
    domain: str
    status: str
    target_ips: List[str] = field(default_factory=list)
    current_ips: List[str] = field(default_factory=list)
    added_ips: List[str] = field(default_factory=list)
    deleted_ips: List[str] = field(default_factory=list)
    unchanged_ips: List[str] = field(default_factory=list)


@dataclass
class SyncResult:
    status: str  # "success", "no_change", "skipped_protection", "dry_run", "error"
    target_ips: List[str] = field(default_factory=list)
    details: List[DomainSyncDetail] = field(default_factory=list)
    message: str = ""


class DNSUpdater:
    """Manages DNS state comparison and safe synchronization for Cloudflare."""

    def __init__(self, cf_client: CloudflareClient, cf_config: Dict[str, Any], filter_config: Dict[str, Any]):
        self.client = cf_client
        self.cf_config = cf_config
        self.filter_config = filter_config

        self.zone_id = cf_config.get("zone_id", "").strip()
        self.zone_name = cf_config.get("zone_name", "").strip()

        # Support both record_names (list) and record_name (string/list/comma-separated)
        raw_names = cf_config.get("record_names") or cf_config.get("record_name") or []
        if isinstance(raw_names, str):
            self.record_names = [n.strip() for n in raw_names.split(",") if n.strip()]
        elif isinstance(raw_names, list):
            self.record_names = [str(n).strip() for n in raw_names if str(n).strip()]
        else:
            self.record_names = []

        if not self.record_names:
            raise ValueError("配置文件中缺少解析域名设置 (需配置 'record_names' 或 'record_name')。")

        self.record_type = cf_config.get("record_type", "A").strip().upper()
        self.ttl = int(cf_config.get("ttl", 60))
        self.proxied = bool(cf_config.get("proxied", False))
        self.comment = cf_config.get("comment", "Auto-updated by CloudflareSpeedTest")

        self.min_qualified_ips = int(filter_config.get("min_qualified_ips", 3))

    def preflight_check(self) -> str:
        """[Protection 2] Verify API Token and resolve Zone ID before any changes."""
        logger.info("[防护检查 2] 验证 Cloudflare API 连通性与权限...")
        if not self.client.verify_token():
            raise CloudflareAPIError("Cloudflare API Token 无效或未激活，中止更新以保护线上 DNS。")

        if not self.zone_id:
            if not self.zone_name:
                raise ValueError("配置文件中缺少 'zone_id' 和 'zone_name'，至少需提供其中一项。")
            self.zone_id = self.client.get_zone_id(self.zone_name)

        return self.zone_id

    def sync(self, candidates: List[IPResult], dry_run: bool = False) -> SyncResult:
        """Execute the safe DNS synchronization workflow across all configured domains."""
        # 1. Protection 1 & 4: Check minimum qualified IPs and empty results first!
        # If no qualified IPs were found, abort immediately to protect existing online DNS
        # and avoid unnecessary network calls to Cloudflare API.
        if not candidates or len(candidates) < self.min_qualified_ips:
            msg = (
                f"[防护拦截 1&4] 测速达标 IP 数量不足 ({len(candidates)} < 最低阈值 {self.min_qualified_ips})！\n"
                f"为保护现有线上解析稳定，本次自动放弃更新，保留上一轮全部 DNS 记录。"
            )
            logger.warning(msg)
            return SyncResult(
                status="skipped_protection",
                target_ips=[c.ip for c in candidates],
                message=msg,
            )

        # 2. Protection 2: Preflight check (only when we actually have enough IPs to update!)
        zone_id = self.preflight_check()

        target_ips = [c.ip for c in candidates]
        logger.info(
            "目标解析域名 (%d 个): %s [类型: %s, Proxied: %s]",
            len(self.record_names),
            ", ".join(self.record_names),
            self.record_type,
            self.proxied,
        )
        logger.info("本次拟更新的优选 IP 列表 (%d 个): %s", len(target_ips), ", ".join(target_ips))

        details: List[DomainSyncDetail] = []
        overall_status = "no_change"

        for domain in self.record_names:
            logger.info("--- 开始同步域名: %s ---", domain)
            # 3. Fetch existing DNS records for this specific domain
            existing_records = self.client.list_records(zone_id, domain, self.record_type)
            current_map: Dict[str, str] = {rec["content"]: rec["id"] for rec in existing_records}
            current_ips = list(current_map.keys())

            logger.info(
                "[%s] Cloudflare 当前已有解析 IP (%d 个): %s",
                domain,
                len(current_ips),
                ", ".join(current_ips) if current_ips else "(空)",
            )

            # Calculate Diff
            target_set = set(target_ips)
            current_set = set(current_ips)

            to_add = [ip for ip in target_ips if ip not in current_set]
            to_delete_ips = [ip for ip in current_ips if ip not in target_set]
            unchanged = [ip for ip in target_ips if ip in current_set]

            logger.info(
                "[%s] 变更分析: 保持不变 = %d 个, 需新增 = %d 个, 需删除旧记录 = %d 个",
                domain,
                len(unchanged),
                len(to_add),
                len(to_delete_ips),
            )

            if not to_add and not to_delete_ips:
                logger.info("[%s] 线上 DNS 记录与优选结果完全一致，无需变更。", domain)
                details.append(
                    DomainSyncDetail(
                        domain=domain,
                        status="no_change",
                        target_ips=target_ips,
                        current_ips=current_ips,
                        unchanged_ips=unchanged,
                    )
                )
                continue

            if dry_run:
                logger.info("[%s] [Dry Run 试运行模式] 将新增: %s", domain, to_add)
                logger.info("[%s] [Dry Run 试运行模式] 将删除: %s", domain, to_delete_ips)
                details.append(
                    DomainSyncDetail(
                        domain=domain,
                        status="dry_run",
                        target_ips=target_ips,
                        current_ips=current_ips,
                        added_ips=to_add,
                        deleted_ips=to_delete_ips,
                        unchanged_ips=unchanged,
                    )
                )
                overall_status = "dry_run"
                continue

            # 4. Protection 3: Add new IPs FIRST before deleting old ones!
            added_successfully: List[str] = []
            for ip in to_add:
                try:
                    self.client.create_record(
                        zone_id=zone_id,
                        record_type=self.record_type,
                        name=domain,
                        content=ip,
                        ttl=self.ttl,
                        proxied=self.proxied,
                        comment=self.comment,
                    )
                    added_successfully.append(ip)
                except Exception as e:
                    logger.error("[%s] 新增 DNS 记录失败 [%s -> %s]: %s", domain, domain, ip, e)

            # Safety sanity check before deletion:
            active_ip_count = len(unchanged) + len(added_successfully)
            if active_ip_count < self.min_qualified_ips:
                msg = (
                    f"[{domain}] [紧急熔断] 新记录添加后活跃 IP 仅为 {active_ip_count}，低于最低保护阈值 {self.min_qualified_ips}！"
                    f"暂停执行删除旧记录操作，防止造成断网。"
                )
                logger.error(msg)
                details.append(
                    DomainSyncDetail(
                        domain=domain,
                        status="error",
                        target_ips=target_ips,
                        current_ips=current_ips,
                        added_ips=added_successfully,
                        unchanged_ips=unchanged,
                    )
                )
                overall_status = "error"
                continue

            # 5. Delete obsolete records
            deleted_successfully: List[str] = []
            for old_ip in to_delete_ips:
                rec_id = current_map[old_ip]
                try:
                    self.client.delete_record(zone_id, rec_id)
                    deleted_successfully.append(old_ip)
                except Exception as e:
                    logger.error("[%s] 删除旧 DNS 记录失败 [ID: %s, IP: %s]: %s", domain, rec_id, old_ip, e)

            logger.info(
                "[%s] DNS 更新完成: 保持 %d 个, 新增 %d 个, 清理旧记录 %d 个。",
                domain,
                len(unchanged),
                len(added_successfully),
                len(deleted_successfully),
            )
            details.append(
                DomainSyncDetail(
                    domain=domain,
                    status="success",
                    target_ips=target_ips,
                    current_ips=current_ips,
                    added_ips=added_successfully,
                    deleted_ips=deleted_successfully,
                    unchanged_ips=unchanged,
                )
            )
            overall_status = "success"

        return SyncResult(
            status=overall_status,
            target_ips=target_ips,
            details=details,
            message=f"Sync completed across {len(self.record_names)} domains.",
        )
