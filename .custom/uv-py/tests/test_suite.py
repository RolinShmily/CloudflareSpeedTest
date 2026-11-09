"""Unit tests for cfst_ddns logic."""

import argparse
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock, patch

src_dir = Path(__file__).resolve().parent.parent / "src"
if str(src_dir) not in sys.path:
    sys.path.insert(0, str(src_dir))

from cfst_ddns import main as main_module
from cfst_ddns.cloudflare import CloudflareClient
from cfst_ddns.filter import IPResult, expand_colo_set, filter_and_rank_ips, get_ip_subnet, parse_csv
from cfst_ddns.notify import DEFAULT_EVENTS, CycleReport, WebhookNotifier
from cfst_ddns.sync import DNSUpdater


class TestDDNSFilterAndSync(unittest.TestCase):
    def test_csv_parsing_and_filtering(self):
        with TemporaryDirectory() as tmpdir:
            csv_path = Path(tmpdir) / "test_result.csv"
            csv_content = (
                "IP 地址,已发送,已接收,丢包率,平均延迟,下载速度(MB/s),地区码\n"
                "104.27.200.1,4,4,0.00,120.50,35.20,LAX\n"
                "104.27.200.2,4,4,0.00,140.10,25.40,SJC\n"
                "104.27.200.3,4,4,0.00,160.00,20.00,HKG\n"
                "104.27.200.4,4,4,0.00,180.00,15.00,NRT\n"
                "104.27.200.5,4,4,0.00,195.00,10.00,KHH\n"
                "104.27.200.6,4,2,0.50,150.00,30.00,LAX\n"  # High loss
                "104.27.200.7,4,4,0.00,280.00,30.00,FRA\n"  # High delay
                "104.27.200.8,4,4,0.00,130.00,2.00,LAX\n"   # Low speed
                "192.168.1.1,4,4,0.00,50.00,100.00,LAN\n"   # Private IP
            )
            csv_path.write_text(csv_content, encoding="utf-8")

            results = parse_csv(csv_path, record_type="A")
            self.assertEqual(len(results), 8)

            filter_cfg = {
                "max_delay_ms": 200.0,
                "max_loss_rate": 0.05,
                "min_speed_mb": 5.0,
                "target_ip_count": 3,
                "min_qualified_ips": 3,
            }
            selected = filter_and_rank_ips(results, filter_cfg)
            self.assertEqual(len(selected), 3)
            self.assertEqual(selected[0].ip, "104.27.200.1")
            self.assertEqual(selected[1].ip, "104.27.200.2")
            self.assertEqual(selected[2].ip, "104.27.200.3")

            # Test sort_by delay
            filter_cfg_delay = dict(filter_cfg, sort_by="delay")
            selected_delay = filter_and_rank_ips(results, filter_cfg_delay)
            # The lowest delay IP among qualified ones is 104.27.200.1 (120.50ms) then 104.27.200.2 (140.10ms)
            self.assertEqual(selected_delay[0].ip, "104.27.200.1")

            # Test min_delay_ms filtering
            filter_cfg_min_delay = dict(filter_cfg, min_delay_ms=150.0)
            selected_min = filter_and_rank_ips(results, filter_cfg_min_delay)
            # 104.27.200.1 (120ms) and 104.27.200.2 (140ms) filtered out
            for item in selected_min:
                self.assertGreaterEqual(item.delay_ms, 150.0)

            # Test exclude_colo (e.g. exclude HKG)
            filter_cfg_exclude = dict(filter_cfg, exclude_colo=["HKG"], sort_by="delay")
            selected_exclude = filter_and_rank_ips(results, filter_cfg_exclude)
            for item in selected_exclude:
                self.assertNotEqual(item.colo.upper(), "HKG")
            # In results: HKG is 104.27.200.3, it should not be in selected_exclude
            self.assertNotIn("104.27.200.3", [x.ip for x in selected_exclude])

    def test_region_diversification_and_elastic_fallback(self):
        """Test 100-to-10 selection with macro-region filtering, round-robin cap, and elastic fallback."""
        # Verify macro-region expansion (JP, SG, KR, TW without US/EU/HK)
        allowed = expand_colo_set(["JP", "SG", "KR", "TW"])
        self.assertIn("NRT", allowed)
        self.assertIn("SIN", allowed)
        self.assertIn("ICN", allowed)
        self.assertIn("TPE", allowed)
        self.assertNotIn("SJC", allowed)
        self.assertNotIn("FRA", allowed)
        self.assertNotIn("HKG", allowed)

        # Simulate a candidate pool heavily dominated by NRT (40 IPs), plus KIX, SIN, ICN, TPE, KHH, and excluded LAX/FRA/HKG
        candidates = []
        for i in range(1, 41):
            # NRT has lowest delay (40ms~44ms)
            candidates.append(IPResult(f"104.16.1.{i}", 4, 4, 0.0, 40.0 + i * 0.1, 30.0, "NRT"))
        for i in range(1, 5):
            candidates.append(IPResult(f"104.16.2.{i}", 4, 4, 0.0, 48.0 + i, 25.0, "KIX"))
        for i in range(1, 4):
            candidates.append(IPResult(f"104.16.3.{i}", 4, 4, 0.0, 65.0 + i, 20.0, "SIN"))
        for i in range(1, 4):
            candidates.append(IPResult(f"104.16.4.{i}", 4, 4, 0.0, 55.0 + i, 22.0, "ICN"))
        for i in range(1, 4):
            candidates.append(IPResult(f"104.16.5.{i}", 4, 4, 0.0, 60.0 + i, 18.0, "TPE"))
        # US (LAX), Western Europe (FRA) and HKG should be excluded by include_colo/exclude_colo
        candidates.append(IPResult("104.16.6.1", 4, 4, 0.0, 35.0, 50.0, "LAX"))
        candidates.append(IPResult("104.16.7.1", 4, 4, 0.0, 30.0, 60.0, "FRA"))
        candidates.append(IPResult("104.16.8.1", 4, 4, 0.0, 25.0, 65.0, "HKG"))

        filter_cfg = {
            "target_ip_count": 10,
            "min_qualified_ips": 5,
            "sort_by": "delay",
            "diversify_colo": True,
            "max_per_colo": 2,
            "strict_colo_cap": False,
            "include_colo": ["JP", "SG", "KR", "TW"],
            "exclude_colo": ["HKG", "EU"],
            "max_delay_ms": 200.0,
            "max_loss_rate": 0.05,
            "min_speed_mb": 5.0,
        }

        selected = filter_and_rank_ips(candidates, filter_cfg)
        self.assertEqual(len(selected), 10)
        selected_colos = [x.colo for x in selected]

        # LAX, FRA and HKG must never appear
        self.assertNotIn("LAX", selected_colos)
        self.assertNotIn("FRA", selected_colos)
        self.assertNotIn("HKG", selected_colos)

        # All 5 Asian colos (NRT, KIX, SIN, ICN, TPE) get 2 IPs each = 10 IPs total!
        for expected_colo in ("NRT", "KIX", "SIN", "ICN", "TPE"):
            self.assertEqual(selected_colos.count(expected_colo), 2)

        # Now test elastic fallback when only 2 colos (NRT: 10, SIN: 10) exist in the candidate pool
        scarce_candidates = [
            IPResult(f"104.18.1.{i}", 4, 4, 0.0, 45.0 + i, 25.0, "NRT") for i in range(1, 11)
        ] + [
            IPResult(f"104.18.2.{i}", 4, 4, 0.0, 65.0 + i, 20.0, "SIN") for i in range(1, 11)
        ]

        # Elastic fallback (strict_colo_cap=False): fills to 10 evenly (5 NRT + 5 SIN)
        selected_elastic = filter_and_rank_ips(scarce_candidates, filter_cfg)
        self.assertEqual(len(selected_elastic), 10)
        elastic_colos = [x.colo for x in selected_elastic]
        self.assertEqual(elastic_colos.count("NRT"), 5)
        self.assertEqual(elastic_colos.count("SIN"), 5)

        # Strict cap (strict_colo_cap=True): strictly stops at 2 NRT + 2 SIN = 4 IPs
        strict_cfg = dict(filter_cfg, strict_colo_cap=True)
        selected_strict = filter_and_rank_ips(scarce_candidates, strict_cfg)
        self.assertEqual(len(selected_strict), 4)

    def test_protection_min_qualified_ips(self):
        mock_client = MagicMock(spec=CloudflareClient)
        mock_client.verify_token.return_value = True

        cf_config = {
            "zone_id": "dummy_zone",
            "record_name": "*.cf.example.com",
            "record_type": "A",
            "ttl": 60,
            "proxied": False,
        }
        filter_config = {
            "min_qualified_ips": 3,
        }
        updater = DNSUpdater(mock_client, cf_config, filter_config)

        candidates = [
            IPResult("104.27.200.1", 4, 4, 0.0, 100.0, 20.0),
            IPResult("104.27.200.2", 4, 4, 0.0, 110.0, 15.0),
        ]

        result = updater.sync(candidates)
        self.assertEqual(result.status, "skipped_protection")
        mock_client.create_record.assert_not_called()
        mock_client.delete_record.assert_not_called()

    def test_sync_both_wildcard_and_apex(self):
        """Test syncing BOTH *.cf.example.com and cf.example.com simultaneously."""
        mock_client = MagicMock(spec=CloudflareClient)
        mock_client.verify_token.return_value = True

        # Existing records
        def mock_list(zone_id, name, record_type):
            return [
                {"id": f"rec_{name}_1", "name": name, "content": "104.27.200.1", "ttl": 60, "proxied": False},
                {"id": f"rec_{name}_2", "name": name, "content": "104.27.200.2", "ttl": 60, "proxied": False},
                {"id": f"rec_{name}_3", "name": name, "content": "104.27.200.3", "ttl": 60, "proxied": False},
            ]

        mock_client.list_records.side_effect = mock_list

        cf_config = {
            "zone_id": "dummy_zone",
            "record_names": ["*.cf.example.com", "cf.example.com"],
            "record_type": "A",
            "ttl": 60,
            "proxied": False,
        }
        filter_config = {
            "min_qualified_ips": 3,
        }
        updater = DNSUpdater(mock_client, cf_config, filter_config)

        candidates = [
            IPResult("104.27.200.2", 4, 4, 0.0, 100.0, 30.0),
            IPResult("104.27.200.3", 4, 4, 0.0, 105.0, 25.0),
            IPResult("104.27.200.4", 4, 4, 0.0, 110.0, 20.0),
            IPResult("104.27.200.5", 4, 4, 0.0, 115.0, 18.0),
        ]

        call_order = []
        mock_client.create_record.side_effect = lambda **kwargs: call_order.append(f"CREATE {kwargs['name']} {kwargs['content']}") or {"id": "new"}
        mock_client.delete_record.side_effect = lambda zone_id, rec_id: call_order.append(f"DELETE {rec_id}") or True

        result = updater.sync(candidates)
        self.assertEqual(result.status, "success")
        self.assertEqual(len(result.details), 2)

        # Check detail for wildcard
        wildcard_detail = result.details[0]
        self.assertEqual(wildcard_detail.domain, "*.cf.example.com")
        self.assertEqual(wildcard_detail.added_ips, ["104.27.200.4", "104.27.200.5"])
        self.assertEqual(wildcard_detail.deleted_ips, ["104.27.200.1"])

        # Check detail for apex
        apex_detail = result.details[1]
        self.assertEqual(apex_detail.domain, "cf.example.com")
        self.assertEqual(apex_detail.added_ips, ["104.27.200.4", "104.27.200.5"])
        self.assertEqual(apex_detail.deleted_ips, ["104.27.200.1"])

        # Check call order: CREATE happens before DELETE for each domain
        for domain, rec_id in [("*.cf.example.com", "rec_*.cf.example.com_1"), ("cf.example.com", "rec_cf.example.com_1")]:
            c_idx = call_order.index(f"CREATE {domain} 104.27.200.4")
            d_idx = call_order.index(f"DELETE {rec_id}")
            self.assertLess(c_idx, d_idx)

    def test_subnet_diversification_and_loss_tier_sorting(self):
        """Test that /24 subnet limits prevent clustering and 0% loss IPs rank first."""
        # 1. Test get_ip_subnet helper
        self.assertEqual(get_ip_subnet("104.21.35.12"), "104.21.35.0")
        self.assertEqual(get_ip_subnet("2606:4700:3030::6815:230c"), "2606:4700:3030::")

        # 2. Test 0% loss prioritization over slightly lower delay with loss
        candidates = [
            # IP A has 120ms delay but 4% packet loss (0.04)
            IPResult("104.16.1.1", 100, 96, 0.04, 120.0, 20.0, "NRT"),
            # IP B has 122ms delay but 0% packet loss (0.00)
            IPResult("104.16.2.1", 100, 100, 0.00, 122.0, 20.0, "NRT"),
        ]
        cfg = {
            "target_ip_count": 1,
            "min_qualified_ips": 1,
            "sort_by": "delay",
            "diversify_colo": False,
        }
        selected = filter_and_rank_ips(candidates, cfg)
        self.assertEqual(len(selected), 1)
        # IP B (0% loss) must be selected over IP A (4% loss) despite having 2ms higher delay
        self.assertEqual(selected[0].ip, "104.16.2.1")

        # 3. Test max_per_subnet prevents /24 clustering
        # Pool with 4 IPs from 104.16.1.0/24 (slightly faster) and 4 IPs from 104.16.2.0/24
        subnet_candidates = [
            IPResult(f"104.16.1.{i}", 4, 4, 0.0, 100.0 + i, 20.0, "NRT") for i in range(1, 5)
        ] + [
            IPResult(f"104.16.2.{i}", 4, 4, 0.0, 110.0 + i, 20.0, "NRT") for i in range(1, 5)
        ]

        # Request 4 IPs with max_per_subnet = 1
        cfg_subnet = {
            "target_ip_count": 4,
            "min_qualified_ips": 2,
            "sort_by": "delay",
            "diversify_colo": False,
            "max_per_subnet": 1,
            "strict_subnet_cap": False,
        }
        selected_sub = filter_and_rank_ips(subnet_candidates, cfg_subnet)
        subnets = [get_ip_subnet(x.ip) for x in selected_sub]
        # Should pick 1 from 104.16.1.0/24 and 1 from 104.16.2.0/24 in pass 1,
        # then fallback to fill up to 4
        self.assertEqual(len(selected_sub), 4)


class TestWebhookNotifier(unittest.TestCase):
    """验证 Webhook 通知模块的配置解析、消息渲染与发送降级行为。"""

    FEISHU_URL = "https://open.feishu.cn/open-apis/bot/v2/hook/0000-test-token"

    def _report(self, **kwargs):
        base = dict(
            status="success",
            target_ips=["104.16.1.1", "104.16.2.2"],
            domains=[
                {
                    "domain": "*.cf.example.com",
                    "status": "success",
                    "added_ips": ["104.16.1.1"],
                    "deleted_ips": ["104.16.9.9"],
                    "unchanged_ips": ["104.16.2.2"],
                }
            ],
            duration=42.5,
        )
        base.update(kwargs)
        return CycleReport(**base)

    def test_provider_detection(self):
        self.assertEqual(WebhookNotifier._detect_provider(self.FEISHU_URL), "feishu")
        self.assertEqual(
            WebhookNotifier._detect_provider("https://open.larksuite.com/open-apis/bot/v2/hook/x"), "feishu"
        )
        self.assertEqual(WebhookNotifier._detect_provider("https://oapi.dingtalk.com/robot/send?x=1"), "dingtalk")
        self.assertEqual(WebhookNotifier._detect_provider("https://qyapi.weixin.qq.com/cgi-bin/webhook/send?k=1"), "wecom")
        self.assertEqual(WebhookNotifier._detect_provider("https://example.com/hook"), "generic")
        self.assertEqual(WebhookNotifier._detect_provider(""), "generic")

    def test_enabled_defaults_and_empty_url(self):
        # 显式未配置 enabled，但有 URL -> 自动启用
        self.assertTrue(WebhookNotifier.from_config({"webhook_url": self.FEISHU_URL}).enabled)
        # 无 URL -> 禁用
        self.assertFalse(WebhookNotifier.from_config({}).enabled)
        # 显式关闭
        self.assertFalse(WebhookNotifier.from_config({"enabled": False, "webhook_url": self.FEISHU_URL}).enabled)

    def test_render_contains_core_fields(self):
        notifier = WebhookNotifier.from_config({"webhook_url": self.FEISHU_URL})
        title, body = notifier._render(self._report())
        self.assertIn("更新成功", title)
        self.assertIn("success", body)
        self.assertIn("*.cf.example.com", body)
        self.assertIn("104.16.1.1", body)
        self.assertIn("新增 1", body)
        self.assertIn("删除 1", body)
        self.assertIn("42.5s", body)

    def test_feishu_payload_and_signature(self):
        notifier = WebhookNotifier.from_config(
            {"webhook_url": self.FEISHU_URL, "secret": "test-secret", "msg_type": "interactive"}
        )
        payload = notifier._build_payload("标题", "正文", "error", self._report(status="error", error="boom"))
        self.assertEqual(payload["msg_type"], "interactive")
        self.assertEqual(payload["card"]["header"]["template"], "red")
        self.assertIn("正文", payload["card"]["elements"][0]["text"]["content"])

        # 签名算法固定向量，防止回归（HMAC-SHA256(key="ts\nsecret", msg="") -> base64）
        self.assertEqual(
            WebhookNotifier._feishu_sign("sec", 1700000000),
            "ttGYnPblC3CcNExnS7y5igm57JhpqZJtHpJ+TURiUeg=",
        )

    def test_text_mode_payload(self):
        notifier = WebhookNotifier.from_config({"webhook_url": self.FEISHU_URL, "msg_type": "text"})
        payload = notifier._build_payload("标题", "**状态**: ok", "success", self._report())
        self.assertEqual(payload["msg_type"], "text")
        self.assertNotIn("**", payload["content"]["text"])

    def test_events_and_dry_run_filtering(self):
        notifier = WebhookNotifier.from_config({"webhook_url": self.FEISHU_URL, "events": ["error"]})
        with unittest.mock.patch.object(WebhookNotifier, "_post", return_value=True) as mocked:
            self.assertFalse(notifier.notify_cycle(self._report(status="success")))
            mocked.assert_not_called()
            self.assertTrue(notifier.notify_cycle(self._report(status="error")))
            mocked.assert_called_once()

        notifier_all = WebhookNotifier.from_config({"webhook_url": self.FEISHU_URL})
        self.assertEqual(notifier_all.events, list(DEFAULT_EVENTS))
        with unittest.mock.patch.object(WebhookNotifier, "_post", return_value=True):
            # Dry Run 默认不推送
            self.assertFalse(notifier_all.notify_cycle(self._report(status="dry_run", dry_run=True)))
        notifier_dry = WebhookNotifier.from_config(
            {"webhook_url": self.FEISHU_URL, "notify_on_dry_run": True}
        )
        with unittest.mock.patch.object(WebhookNotifier, "_post", return_value=True):
            self.assertTrue(notifier_dry.notify_cycle(self._report(status="dry_run", dry_run=True)))

    def test_disabled_never_posts(self):
        notifier = WebhookNotifier.from_config({"enabled": False, "webhook_url": self.FEISHU_URL})
        with unittest.mock.patch.object(WebhookNotifier, "_post", return_value=True) as mocked:
            self.assertFalse(notifier.notify_cycle(self._report()))
            mocked.assert_not_called()

    def test_post_failure_is_swallowed(self):
        import urllib.error

        notifier = WebhookNotifier.from_config({"webhook_url": self.FEISHU_URL, "max_retries": 0})
        opener = MagicMock()
        opener.open.side_effect = urllib.error.URLError("network down")
        with patch("cfst_ddns.notify.urllib.request.build_opener", return_value=opener):
            self.assertFalse(notifier.notify_cycle(self._report()))

    def test_post_success_with_business_code(self):
        def fake_opener(payload_code):
            opener = MagicMock()
            resp = MagicMock()
            resp.read.return_value = ('{"code": %d, "msg": "ok"}' % payload_code).encode("utf-8")
            resp.__enter__ = MagicMock(return_value=resp)
            resp.__exit__ = MagicMock(return_value=False)
            opener.open.return_value = resp
            return opener

        notifier = WebhookNotifier.from_config({"webhook_url": self.FEISHU_URL, "max_retries": 0})
        with patch(
            "cfst_ddns.notify.urllib.request.build_opener", return_value=fake_opener(0)
        ):
            self.assertTrue(notifier.notify_cycle(self._report()))
        with patch(
            "cfst_ddns.notify.urllib.request.build_opener", return_value=fake_opener(19021)
        ):
            self.assertFalse(notifier.notify_cycle(self._report()))

    def test_generic_payload_structure(self):
        notifier = WebhookNotifier.from_config({"webhook_url": "https://example.com/hook"})
        payload = notifier._build_payload("标题", "正文", "success", self._report())
        self.assertEqual(payload["status"], "success")
        self.assertEqual(payload["target_ips"], ["104.16.1.1", "104.16.2.2"])
        self.assertIn("正文", payload["text"])
        self.assertIn("timestamp", payload)


class TestCycleNotificationIntegration(unittest.TestCase):
    """确认 execute_cycle 在各类退出路径上推送通知，且通知故障不影响退出码。"""

    def _args(self, **kwargs):
        base = dict(dry_run=False, verbose=False, verify_only=False, no_notify=False)
        base.update(kwargs)
        return argparse.Namespace(**base)

    def _config(self):
        return {"notify": {"webhook_url": "https://open.feishu.cn/open-apis/bot/v2/hook/test"}}

    def test_notify_on_success(self):
        def fake_core(config, args, report):
            report.status = "success"
            report.target_ips = ["1.1.1.1"]
            return 0

        with patch.object(main_module, "_execute_cycle_core", side_effect=fake_core), patch.object(
            WebhookNotifier, "notify_cycle", return_value=True
        ) as notify:
            code = main_module.execute_cycle(self._config(), self._args())
        self.assertEqual(code, 0)
        notify.assert_called_once()
        report = notify.call_args[0][0]
        self.assertEqual(report.status, "success")
        self.assertEqual(report.target_ips, ["1.1.1.1"])
        self.assertGreaterEqual(report.duration, 0.0)

    def test_notify_swallows_notifier_exception(self):
        with patch.object(main_module, "_execute_cycle_core", return_value=0), patch.object(
            WebhookNotifier, "notify_cycle", side_effect=RuntimeError("boom")
        ):
            self.assertEqual(main_module.execute_cycle(self._config(), self._args()), 0)

    def test_core_exception_becomes_error_report(self):
        with patch.object(main_module, "_execute_cycle_core", side_effect=RuntimeError("kaboom")), patch.object(
            WebhookNotifier, "notify_cycle", return_value=True
        ) as notify:
            self.assertEqual(main_module.execute_cycle(self._config(), self._args()), 1)
        report = notify.call_args[0][0]
        self.assertEqual(report.status, "error")
        self.assertIn("kaboom", report.error)

    def test_verify_only_and_no_notify_skip_push(self):
        with patch.object(main_module, "_execute_cycle_core", return_value=0), patch.object(
            WebhookNotifier, "notify_cycle", return_value=True
        ) as notify:
            main_module.execute_cycle(self._config(), self._args(verify_only=True))
            main_module.execute_cycle(self._config(), self._args(no_notify=True))
        notify.assert_not_called()


if __name__ == "__main__":
    unittest.main()
