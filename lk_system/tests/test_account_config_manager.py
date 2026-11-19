import unittest
import os
import sys
import tempfile
import json
from types import SimpleNamespace

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from core.account_config_manager import AccountConfigManager


class TestAccountConfigManager(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.config_path = os.path.join(self.temp_dir.name, "test_accounts_config.json")
        self.mgr = AccountConfigManager(config_path=self.config_path)

        # Mock MT5 account info
        self.mock_acc = SimpleNamespace(
            login=213877054,
            balance=5000.0,
            equity=5000.0,
            server="OctaFX-Demo",
            company="MetaQuotes Ltd."
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_new_account_setup_defaults(self):
        cfg = self.mgr.get_or_setup_account(self.mock_acc)
        self.assertEqual(cfg["account_id"], "213877054")
        self.assertEqual(cfg["account_lifecycle"], "challenge")
        self.assertEqual(cfg["current_phase"], 1)
        self.assertEqual(cfg["phase_start_balance"], 5000.0)
        self.assertEqual(cfg["phase_1_target_pct"], 8.0)
        self.assertEqual(cfg["phase_2_target_pct"], 5.0)
        self.assertEqual(cfg["challenge_risk_pct"], 1.25)
        self.assertEqual(cfg["funded_risk_pct"], 1.0)
        self.assertEqual(cfg["risk_per_trade"], 0.01) # Default fixed 1.00%
        self.assertFalse(cfg["use_custom_phase_risk"])
        self.assertFalse(cfg["target_locked"])

        # Check persistence to disk
        self.assertTrue(os.path.exists(self.config_path))
        with open(self.config_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        self.assertIn("213877054", data)

    def test_advance_phase_progression(self):
        self.mgr.get_or_setup_account(self.mock_acc)

        # Advance to Phase 2 with new balance $5,400.00
        ok = self.mgr.advance_account_phase("213877054", new_phase=2, new_start_bal=5400.0)
        self.assertTrue(ok)
        cfg = self.mgr.accounts["213877054"]
        self.assertEqual(cfg["current_phase"], 2)
        self.assertEqual(cfg["phase_start_balance"], 5400.0)

        # Advance to Funded Mode with balance $5,670.00
        ok = self.mgr.advance_account_phase("213877054", new_phase="funded", new_start_bal=5670.0)
        self.assertTrue(ok)
        cfg = self.mgr.accounts["213877054"]
        self.assertEqual(cfg["account_lifecycle"], "funded")
        self.assertEqual(cfg["current_phase"], "funded")
        self.assertEqual(cfg["phase_start_balance"], 5670.0)

    def test_custom_phase_risk_scaling_toggle(self):
        self.mgr.get_or_setup_account(self.mock_acc)

        # Toggle ON
        is_on = self.mgr.toggle_custom_phase_risk("213877054")
        self.assertTrue(is_on)
        cfg = self.mgr.accounts["213877054"]
        self.assertTrue(cfg["use_custom_phase_risk"])
        self.assertEqual(cfg["risk_per_trade"], 0.0125) # 1.25% in challenge mode

        # Advance to funded, risk should scale to 1.00%
        self.mgr.advance_account_phase("213877054", new_phase="funded")
        cfg = self.mgr.accounts["213877054"]
        self.assertEqual(cfg["risk_per_trade"], 0.0100) # 1.00% in funded mode

        # Toggle OFF -> fixed 1.00%
        is_on = self.mgr.toggle_custom_phase_risk("213877054")
        self.assertFalse(is_on)
        cfg = self.mgr.accounts["213877054"]
        self.assertEqual(cfg["risk_per_trade"], 0.0100)

    def test_target_progress_and_locking(self):
        self.mgr.get_or_setup_account(self.mock_acc)

        # 1. Start balance 5000, current balance 5200 (+4.0% -> not yet target +8.0%)
        is_reached, prog = self.mgr.check_target_progress("213877054", current_balance=5200.0)
        self.assertFalse(is_reached)
        self.assertAlmostEqual(prog["profit_pct"], 4.0, delta=0.01)
        self.assertEqual(prog["target_dollar"], 5400.0)
        self.assertEqual(prog["remaining_dollar"], 200.0)

        # 2. Current balance 5405 (+8.1% -> Target Achieved!)
        is_reached, prog = self.mgr.check_target_progress("213877054", current_balance=5405.0)
        self.assertTrue(is_reached)
        self.assertEqual(prog["remaining_dollar"], 0.0)

        # 3. Lock target
        self.mgr.set_target_locked("213877054", locked=True)
        self.assertTrue(self.mgr.accounts["213877054"]["target_locked"])

    def test_high_water_mark_tracking(self):
        self.mgr.get_or_setup_account(self.mock_acc)
        hwm = self.mgr.update_high_water_mark("213877054", equity=5250.0)
        self.assertEqual(hwm, 5250.0)

        # Lower equity does not reduce HWM
        hwm2 = self.mgr.update_high_water_mark("213877054", equity=5100.0)
        self.assertEqual(hwm2, 5250.0)

    def test_notification_configuration_and_persistence(self):
        self.mgr.get_or_setup_account(self.mock_acc)

        # 1. Get default notification config
        n_cfg = self.mgr.get_notification_config("213877054")
        self.assertIn("active_platform", n_cfg)

        # 2. Update to Telegram
        ok = self.mgr.update_notification_settings(
            "213877054",
            platform="telegram",
            tg_token="123456789:ABCDEF",
            tg_chat_id="-100987654321"
        )
        self.assertTrue(ok)
        n_cfg2 = self.mgr.get_notification_config("213877054")
        self.assertEqual(n_cfg2["active_platform"], "telegram")
        self.assertEqual(n_cfg2["telegram_bot_token"], "123456789:ABCDEF")
        self.assertEqual(n_cfg2["telegram_chat_id"], "-100987654321")

        # 3. Update to Discord
        ok = self.mgr.update_notification_settings(
            "213877054",
            platform="discord",
            discord_url="https://discord.com/api/webhooks/test/webhook"
        )
        self.assertTrue(ok)
        n_cfg3 = self.mgr.get_notification_config("213877054")
        self.assertEqual(n_cfg3["active_platform"], "discord")
        self.assertEqual(n_cfg3["discord_webhook_url"], "https://discord.com/api/webhooks/test/webhook")
        # Token and chat ID should be preserved
        self.assertEqual(n_cfg3["telegram_bot_token"], "123456789:ABCDEF")

        # 4. Mute notifications
        self.mgr.update_notification_settings("213877054", platform="none")
        self.assertEqual(self.mgr.get_notification_config("213877054")["active_platform"], "none")


if __name__ == "__main__":
    unittest.main()
