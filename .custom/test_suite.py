"""Unit tests for .custom ddns logic."""

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock

from cloudflare import CloudflareClient
from filter import IPResult, filter_and_rank_ips, parse_csv
from sync import DNSUpdater


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


if __name__ == "__main__":
    unittest.main()
