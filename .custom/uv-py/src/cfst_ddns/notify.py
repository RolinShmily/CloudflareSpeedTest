"""Webhook notification module (Feishu / Lark custom bot + generic JSON webhook).

设计原则:
- 零三方依赖 (仅用标准库 urllib)，与项目其余模块保持一致；
- 通知属于「旁路」能力：发送失败只记 WARNING，绝不影响主流程退出码；
- 配置驱动：provider / webhook_url / secret / events 均在 config.json 的 "notify" 段内。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import socket
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

logger = logging.getLogger("cfst_ddns.notify")

# 默认推送的事件集合（可用 config.notify.events 覆盖）
DEFAULT_EVENTS = ("success", "no_change", "skipped_protection", "error")

# status -> (中文标签, 表情, 飞书卡片主题色)
STATUS_STYLE: Dict[str, tuple] = {
    "success": ("更新成功", "✅", "green"),
    "no_change": ("无变更", "➖", "blue"),
    "dry_run": ("试运行", "🧪", "grey"),
    "skipped_protection": ("安全熔断", "🛡️", "orange"),
    "error": ("执行失败", "❌", "red"),
    "verify": ("环境诊断", "🔍", "wathet"),
    "unknown": ("未知状态", "❔", "grey"),
}


@dataclass
class CycleReport:
    """单轮任务的执行结果摘要，供通知模块渲染消息。"""

    status: str = "unknown"
    target_ips: List[str] = field(default_factory=list)
    domains: List[Dict[str, Any]] = field(default_factory=list)
    message: str = ""
    error: str = ""
    dry_run: bool = False
    started_at: float = field(default_factory=time.time)
    duration: float = 0.0


class WebhookNotifier:
    """向飞书自定义机器人（或任意 JSON Webhook）推送任务结果。"""

    def __init__(
        self,
        webhook_url: str = "",
        provider: str = "",
        secret: str = "",
        enabled: Optional[bool] = None,
        events: Optional[List[str]] = None,
        msg_type: str = "interactive",
        timeout: float = 10.0,
        proxy: str = "",
        notify_on_dry_run: bool = False,
        max_retries: int = 1,
    ):
        self.webhook_url = (webhook_url or "").strip()
        self.provider = (provider or "").strip().lower() or self._detect_provider(self.webhook_url)
        self.secret = (secret or "").strip()
        self.events = [str(e).strip().lower() for e in (events or DEFAULT_EVENTS) if str(e).strip()]
        self.msg_type = (msg_type or "interactive").strip().lower()
        self.timeout = float(timeout or 10.0)
        self.proxy = (proxy or "").strip() or None
        self.notify_on_dry_run = bool(notify_on_dry_run)
        self.max_retries = max(0, int(max_retries))

        if enabled is None:
            self.enabled = bool(self.webhook_url)
        else:
            self.enabled = bool(enabled) and bool(self.webhook_url)

        if enabled and not self.webhook_url:
            logger.warning("[通知] 已启用 Webhook 通知但未配置 webhook_url，本次不推送。")

    # ------------------------------------------------------------------ setup
    @classmethod
    def from_config(cls, notify_config: Optional[Dict[str, Any]]) -> "WebhookNotifier":
        cfg = notify_config or {}
        return cls(
            webhook_url=cfg.get("webhook_url", ""),
            provider=cfg.get("provider", ""),
            secret=cfg.get("secret", ""),
            enabled=cfg.get("enabled", None),
            events=cfg.get("events") or list(DEFAULT_EVENTS),
            msg_type=cfg.get("msg_type", "interactive"),
            timeout=cfg.get("timeout", 10.0),
            proxy=cfg.get("proxy", ""),
            notify_on_dry_run=cfg.get("notify_on_dry_run", False),
            max_retries=cfg.get("max_retries", 1),
        )

    @staticmethod
    def _detect_provider(url: str) -> str:
        """依据 Webhook 域名推断平台；无法识别时使用 generic。"""
        if not url:
            return "generic"
        host = (urlparse(url).hostname or "").lower()
        if host.endswith("feishu.cn") or host.endswith("larksuite.com"):
            return "feishu"
        if host.endswith("dingtalk.com"):
            return "dingtalk"
        if host.endswith("weixin.qq.com"):
            return "wecom"
        return "generic"

    # ------------------------------------------------------------- public API
    def notify_cycle(self, report: CycleReport) -> bool:
        """按配置推送单轮任务结果；任何异常都被吞掉并降级为日志。"""
        if not self.enabled:
            logger.debug("[通知] 未启用 Webhook 通知，跳过推送。")
            return False

        status = (report.status or "unknown").lower()
        if report.dry_run and not self.notify_on_dry_run:
            logger.debug("[通知] Dry Run 模式下默认不推送（notify.notify_on_dry_run=false）。")
            return False
        if self.events and status not in self.events:
            # 显式开启 notify_on_dry_run 时，试运行结果不受 events 白名单限制
            if not (report.dry_run and self.notify_on_dry_run):
                logger.info("[通知] 状态 '%s' 不在推送事件列表 %s 中，跳过推送。", status, self.events)
                return False

        title, body = self._render(report)
        return self._dispatch(title=title, body=body, status=status, report=report)

    def send_test(self) -> bool:
        """发送一条测试消息，用于部署后确认 Webhook 可用。"""
        if not self.webhook_url:
            logger.error("[通知] 未配置 notify.webhook_url，无法发送测试消息。")
            return False
        report = CycleReport(
            status="success",
            target_ips=["1.1.1.1", "1.0.0.1"],
            domains=[
                {
                    "domain": "*.cf.example.com",
                    "status": "success",
                    "added_ips": ["1.1.1.1"],
                    "deleted_ips": ["1.0.0.1"],
                    "unchanged_ips": [],
                }
            ],
            message="这是一条来自 cfst-ddns 的测试通知，收到即表示 Webhook 配置成功。",
            duration=0.0,
        )
        title, body = self._render(report, prefix="[测试] ")
        return self._dispatch(title=title, body=body, status="success", report=report)

    # -------------------------------------------------------------- rendering
    def _render(self, report: CycleReport, prefix: str = "") -> "tuple[str, str]":
        label, emoji, _ = STATUS_STYLE.get(report.status.lower(), STATUS_STYLE["unknown"])
        title = f"{prefix}{emoji} CFST DDNS {label}"
        host = self._hostname()
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        lines: List[str] = [
            f"**状态**: {label} ({report.status})",
            f"**域名**: {self._domains_text(report)}",
            f"**目标 IP**: {len(report.target_ips)} 个",
        ]
        if report.target_ips:
            lines.append(f"**IP 列表**: {self._join_ips(report.target_ips)}")

        delta = self._delta_text(report)
        if delta:
            lines.append(f"**变更**: {delta}")

        if report.duration > 0:
            lines.append(f"**耗时**: {report.duration:.1f}s")
        lines.append(f"**主机**: {host}")
        lines.append(f"**时间**: {now}")

        if report.message:
            lines.append("")
            lines.append(f"> {self._truncate(report.message, 500)}")
        if report.error:
            lines.append("")
            lines.append(f"**错误**: {self._truncate(report.error, 800)}")

        return title, "\n".join(lines)

    def _domains_text(self, report: CycleReport) -> str:
        names = [str(d.get("domain", "")).strip() for d in report.domains if d.get("domain")]
        if not names:
            return "-"
        return ", ".join(names)

    @staticmethod
    def _join_ips(ips: List[str], limit: int = 20) -> str:
        shown = [str(ip) for ip in ips[:limit]]
        suffix = f" … (+{len(ips) - limit})" if len(ips) > limit else ""
        return ", ".join(shown) + suffix

    def _delta_text(self, report: CycleReport) -> str:
        added = sum(len(d.get("added_ips") or []) for d in report.domains)
        deleted = sum(len(d.get("deleted_ips") or []) for d in report.domains)
        unchanged = sum(len(d.get("unchanged_ips") or []) for d in report.domains)
        if not report.domains:
            return ""
        parts = [f"新增 {added}", f"删除 {deleted}", f"保持 {unchanged}"]
        if len(report.domains) > 1:
            parts.append(f"跨 {len(report.domains)} 个域名")
        return " / ".join(parts)

    @staticmethod
    def _truncate(text: str, limit: int) -> str:
        text = str(text).replace("\n", " ").strip()
        return text if len(text) <= limit else text[: limit - 1] + "…"

    @staticmethod
    def _hostname() -> str:
        try:
            return socket.gethostname()
        except OSError:  # pragma: no cover - 极端环境
            return "unknown"

    # --------------------------------------------------------------- payloads
    def _build_payload(self, title: str, body: str, status: str, report: CycleReport) -> Dict[str, Any]:
        if self.provider == "feishu":
            return self._build_feishu_payload(title, body, status)
        if self.provider == "dingtalk":
            return {"msgtype": "markdown", "markdown": {"title": title, "text": f"### {title}\n\n{body}"}}
        if self.provider == "wecom":
            return {"msgtype": "markdown", "markdown": {"content": f"### {title}\n\n{body}"}}
        # generic: 尽量给出通用的结构化字段，便于任意接收端消费
        return {
            "title": title,
            "status": status,
            "text": f"{title}\n\n{body}",
            "target_ips": list(report.target_ips),
            "domains": report.domains,
            "message": report.message,
            "error": report.error,
            "duration": round(report.duration, 2),
            "host": self._hostname(),
            "timestamp": datetime.now().isoformat(timespec="seconds"),
        }

    def _build_feishu_payload(self, title: str, body: str, status: str) -> Dict[str, Any]:
        _, _, template = STATUS_STYLE.get(status, STATUS_STYLE["unknown"])
        if self.msg_type == "text":
            return {"msg_type": "text", "content": {"text": f"{title}\n\n{body.replace('**', '')}"}}

        payload: Dict[str, Any] = {
            "msg_type": "interactive",
            "card": {
                "config": {"wide_screen_mode": True, "enable_forward": True},
                "header": {
                    "template": template,
                    "title": {"tag": "plain_text", "content": title},
                },
                "elements": [
                    {"tag": "div", "text": {"tag": "lark_md", "content": body}},
                    {"tag": "hr"},
                    {
                        "tag": "note",
                        "elements": [
                            {"tag": "plain_text", "content": "CloudflareSpeedTest DDNS · 自动推送"}
                        ],
                    },
                ],
            },
        }
        return payload

    @staticmethod
    def _feishu_sign(secret: str, timestamp: int) -> str:
        """飞书自定义机器人「签名校验」：HMAC-SHA256(key=timestamp\\nsecret, msg='') → base64。"""
        string_to_sign = f"{timestamp}\n{secret}"
        digest = hmac.new(string_to_sign.encode("utf-8"), digestmod=hashlib.sha256).digest()
        return base64.b64encode(digest).decode("utf-8")

    # ---------------------------------------------------------------- network
    def _dispatch(self, title: str, body: str, status: str, report: CycleReport) -> bool:
        payload = self._build_payload(title, body, status, report)
        if self.provider == "feishu" and self.secret:
            timestamp = int(time.time())
            payload["timestamp"] = str(timestamp)
            payload["sign"] = self._feishu_sign(self.secret, timestamp)

        try:
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        except (TypeError, ValueError) as e:
            logger.warning("[通知] 消息序列化失败: %s", e)
            return False

        return self._post(data, status)

    def _post(self, data: bytes, status: str) -> bool:
        handlers = []
        if self.proxy:
            handlers.append(urllib.request.ProxyHandler({"http": self.proxy, "https": self.proxy}))
        opener = urllib.request.build_opener(*handlers)

        attempts = self.max_retries + 1
        for attempt in range(1, attempts + 1):
            request = urllib.request.Request(
                self.webhook_url,
                data=data,
                headers={"Content-Type": "application/json; charset=utf-8"},
                method="POST",
            )
            try:
                with opener.open(request, timeout=self.timeout) as resp:
                    raw = resp.read().decode("utf-8", errors="replace")
                if self._is_platform_success(raw):
                    logger.info("[通知] Webhook 推送成功 (status=%s, provider=%s)。", status, self.provider)
                    return True
                logger.warning("[通知] Webhook 返回异常响应 (第 %d/%d 次): %s", attempt, attempts, self._truncate(raw, 300))
            except urllib.error.HTTPError as e:
                detail = ""
                try:
                    detail = e.read().decode("utf-8", errors="replace")
                except OSError:
                    detail = ""
                logger.warning(
                    "[通知] Webhook HTTP 错误 %s (第 %d/%d 次): %s",
                    e.code,
                    attempt,
                    attempts,
                    self._truncate(detail, 300),
                )
            except (urllib.error.URLError, TimeoutError, OSError) as e:
                logger.warning("[通知] Webhook 网络请求失败 (第 %d/%d 次): %s", attempt, attempts, e)

            if attempt < attempts:
                time.sleep(min(2.0 * attempt, 5.0))

        logger.error("[通知] Webhook 推送最终失败，主流程不受影响。")
        return False

    def _is_platform_success(self, raw: str) -> bool:
        """飞书/钉钉/企业微信都会用 code/errcode/StatusCode 表达业务层失败。"""
        if not raw.strip():
            return True
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return True  # 非 JSON 响应交由 HTTP 状态码判定
        if not isinstance(parsed, dict):
            return True

        if self.provider == "feishu":
            code = parsed.get("code", parsed.get("StatusCode", 0))
        elif self.provider == "dingtalk":
            code = parsed.get("errcode", 0)
        elif self.provider == "wecom":
            code = parsed.get("errcode", 0)
        else:
            return True
        try:
            return int(code) == 0
        except (TypeError, ValueError):
            return True
