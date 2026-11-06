"""Cloudflare DNS API v4 client using Python standard library (zero external dependencies)."""

import json
import logging
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional

logger = logging.getLogger("cfst_ddns.cloudflare")


class CloudflareAPIError(Exception):
    """Base exception for Cloudflare API errors."""

    def __init__(self, message: str, status_code: Optional[int] = None, errors: Optional[List[Dict[str, Any]]] = None):
        super().__init__(message)
        self.status_code = status_code
        self.errors = errors or []


class CloudflareAuthError(CloudflareAPIError):
    """Raised when authentication fails or token is invalid."""
    pass


class CloudflareZoneNotFoundError(CloudflareAPIError):
    """Raised when the specified Zone is not found."""
    pass


class CloudflareClient:
    """Client for Cloudflare API v4 DNS management."""

    BASE_URL = "https://api.cloudflare.com/client/v4"

    def __init__(self, api_token: str, timeout: int = 15, proxy: Optional[str] = None):
        if not api_token or api_token == "YOUR_CLOUDFLARE_API_TOKEN":
            raise CloudflareAuthError("Cloudflare API Token 未配置或仍为占位符，请在 config.json 中配置有效的 api_token。")
        self.api_token = api_token.strip()
        self.timeout = timeout
        self.proxy = proxy.strip() if proxy else None

        handlers: List[urllib.request.BaseHandler] = []
        if self.proxy:
            handlers.append(urllib.request.ProxyHandler({"http": self.proxy, "https": self.proxy}))
        self.opener = urllib.request.build_opener(*handlers)

    def _request(self, method: str, endpoint: str, params: Optional[Dict[str, Any]] = None, data: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Send an HTTP request to Cloudflare API v4."""
        url = f"{self.BASE_URL}{endpoint}"
        if params:
            query_string = urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
            url = f"{url}?{query_string}"

        headers = {
            "Authorization": f"Bearer {self.api_token}",
            "Content-Type": "application/json",
            "User-Agent": "CFST-DDNS/1.0",
        }

        body_bytes = None
        if data is not None:
            body_bytes = json.dumps(data).encode("utf-8")

        req = urllib.request.Request(url, data=body_bytes, headers=headers, method=method)

        try:
            with self.opener.open(req, timeout=self.timeout) as resp:
                resp_body = resp.read().decode("utf-8")
                return json.loads(resp_body)
        except urllib.error.HTTPError as e:
            error_text = e.read().decode("utf-8", errors="replace")
            parsed_errors = []
            try:
                err_json = json.loads(error_text)
                parsed_errors = err_json.get("errors", [])
                error_msgs = "; ".join(f"[{item.get('code')}] {item.get('message')}" for item in parsed_errors)
            except Exception:
                error_msgs = error_text

            if e.code in (401, 403):
                raise CloudflareAuthError(
                    f"Cloudflare 认证失败 (HTTP {e.code}): {error_msgs}。请检查 API Token 是否正确并具备对应 Zone 的 DNS:Edit 权限。",
                    status_code=e.code,
                    errors=parsed_errors,
                )
            raise CloudflareAPIError(
                f"Cloudflare 请求失败 (HTTP {e.code}): {error_msgs}",
                status_code=e.code,
                errors=parsed_errors,
            )
        except urllib.error.URLError as e:
            raise CloudflareAPIError(f"连接 Cloudflare API 失败: {e.reason}")
        except Exception as e:
            raise CloudflareAPIError(f"请求 Cloudflare 异常: {str(e)}")

    def verify_token(self) -> bool:
        """Verify the validity of the API token."""
        try:
            res = self._request("GET", "/user/tokens/verify")
            status = res.get("result", {}).get("status")
            if status == "active":
                logger.info("Cloudflare API Token 验证成功 (状态: active)")
                return True
            logger.warning("Cloudflare API Token 验证未激活 (状态: %s)", status)
            return False
        except CloudflareAPIError as e:
            logger.error("Cloudflare API Token 校验失败: %s", e)
            return False

    def get_zone_id(self, zone_name: str) -> str:
        """Find the Zone ID for a given domain name (e.g. 'example.com')."""
        clean_name = zone_name.strip().lstrip("*.")
        logger.debug("正在查询 Zone '%s' 的 ID...", clean_name)
        res = self._request("GET", "/zones", params={"name": clean_name, "status": "active"})
        zones = res.get("result", [])
        if not zones:
            raise CloudflareZoneNotFoundError(
                f"未在 Cloudflare 账户中找到域名 Zone '{clean_name}'，请确认域名已添加至该账户且 Token 具备对应权限。"
            )
        zone_id = zones[0]["id"]
        logger.info("成功获取 Zone '%s' ID: %s", clean_name, zone_id)
        return zone_id

    def list_records(self, zone_id: str, record_name: str, record_type: str = "A") -> List[Dict[str, Any]]:
        """List all DNS records matching the given name and type. Handles pagination."""
        records: List[Dict[str, Any]] = []
        page = 1
        per_page = 100

        while True:
            params = {
                "name": record_name.strip(),
                "type": record_type.strip(),
                "page": page,
                "per_page": per_page,
            }
            res = self._request("GET", f"/zones/{zone_id}/dns_records", params=params)
            batch = res.get("result", [])
            records.extend(batch)

            result_info = res.get("result_info", {})
            total_pages = result_info.get("total_pages", 1)
            if page >= total_pages or not batch:
                break
            page += 1

        logger.debug("查询记录 '%s' (%s) 找到 %d 条记录", record_name, record_type, len(records))
        return records

    def create_record(
        self,
        zone_id: str,
        record_type: str,
        name: str,
        content: str,
        ttl: int = 60,
        proxied: bool = False,
        comment: str = "Auto-updated by CloudflareSpeedTest",
    ) -> Dict[str, Any]:
        """Create a new DNS record."""
        payload = {
            "type": record_type.strip(),
            "name": name.strip(),
            "content": content.strip(),
            "ttl": ttl,
            "proxied": proxied,
            "comment": comment,
        }
        res = self._request("POST", f"/zones/{zone_id}/dns_records", data=payload)
        created = res.get("result", {})
        logger.info("创建 DNS 记录成功: %s -> %s (ID: %s)", name, content, created.get("id"))
        return created

    def delete_record(self, zone_id: str, record_id: str) -> bool:
        """Delete an existing DNS record by its ID."""
        res = self._request("DELETE", f"/zones/{zone_id}/dns_records/{record_id}")
        success = res.get("success", False)
        if success:
            logger.info("删除旧 DNS 记录成功 (ID: %s)", record_id)
        else:
            logger.warning("删除旧 DNS 记录返回非成功状态 (ID: %s)", record_id)
        return success
