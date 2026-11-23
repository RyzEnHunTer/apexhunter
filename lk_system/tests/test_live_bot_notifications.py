import unittest
import os
import sys
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, patch

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from master_live_bot import MasterUnifiedBot


class TestLiveBotNotifications(unittest.TestCase):
    @patch("master_live_bot.mt5")
    def test_session_state_transitions(self, mock_mt5):
        bot = MasterUnifiedBot(dry_run=True, base_capital=5000.0)
        bot.notifier = MagicMock()

        # 1. Test weekend market closed state
        sat_dt = datetime(2026, 11, 21, 10, 0, tzinfo=timezone.utc) # Saturday
        state_sat = bot.get_session_state(sat_dt)
        self.assertEqual(state_sat, "PAUSED_WEEKEND")

        bot.check_session_and_trap_alerts(sat_dt, is_startup=True)
        bot.notifier.notify_trading_paused.assert_called()

        # 2. Test 09:00 UTC Trap Hour
        bot.notifier.reset_mock()
        t09_dt = datetime(2026, 11, 20, 9, 15, tzinfo=timezone.utc)
        state_09 = bot.get_session_state(t09_dt)
        self.assertEqual(state_09, "PAUSED_TRAP_09")
        bot.check_session_and_trap_alerts(t09_dt, is_startup=False)
        bot.notifier.notify_trading_paused.assert_called()

        # 3. Test 10:00 UTC Active London Window
        bot.notifier.reset_mock()
        t10_dt = datetime(2026, 11, 20, 10, 0, tzinfo=timezone.utc)
        state_10 = bot.get_session_state(t10_dt)
        self.assertEqual(state_10, "ACTIVE_LONDON")
        bot.check_session_and_trap_alerts(t10_dt, is_startup=False)
        bot.notifier.notify_trading_resumed.assert_called()

        # 4. Test 12:00 UTC NY Open
        bot.notifier.reset_mock()
        t12_dt = datetime(2026, 11, 20, 12, 0, tzinfo=timezone.utc)
        state_12 = bot.get_session_state(t12_dt)
        self.assertEqual(state_12, "ACTIVE_NY")
        bot.check_session_and_trap_alerts(t12_dt, is_startup=False)
        bot.notifier.notify_session.assert_called()

        # 5. Test 13:00 UTC US Trap Hour
        bot.notifier.reset_mock()
        t13_dt = datetime(2026, 11, 20, 13, 30, tzinfo=timezone.utc)
        state_13 = bot.get_session_state(t13_dt)
        self.assertEqual(state_13, "PAUSED_TRAP_13")
        bot.check_session_and_trap_alerts(t13_dt, is_startup=False)
        bot.notifier.notify_trading_paused.assert_called()

        # 6. Test 14:00 UTC NY Resume
        bot.notifier.reset_mock()
        t14_dt = datetime(2026, 11, 20, 14, 0, tzinfo=timezone.utc)
        state_14 = bot.get_session_state(t14_dt)
        self.assertEqual(state_14, "ACTIVE_NY")
        bot.check_session_and_trap_alerts(t14_dt, is_startup=False)
        bot.notifier.notify_trading_resumed.assert_called()

        # 7. Test 21:00 UTC Asian Range
        bot.notifier.reset_mock()
        t21_dt = datetime(2026, 11, 20, 21, 30, tzinfo=timezone.utc)
        state_21 = bot.get_session_state(t21_dt)
        self.assertEqual(state_21, "PAUSED_ASIAN")
        bot.check_session_and_trap_alerts(t21_dt, is_startup=False)
        bot.notifier.notify_trading_paused.assert_called()

    @patch("master_live_bot.mt5")
    def test_daily_summary_dispatch(self, mock_mt5):
        bot = MasterUnifiedBot(dry_run=True, base_capital=5000.0)
        bot.notifier = MagicMock()

        mock_acc = MagicMock()
        mock_acc.equity = 5120.0
        mock_acc.balance = 5120.0
        mock_mt5.account_info.return_value = mock_acc
        mock_mt5.history_deals_get.return_value = []

        audit_date = datetime(2026, 11, 20).date()
        bot._run_daily_summary(audit_date)
        bot.notifier.notify_daily_summary.assert_called_once()


if __name__ == "__main__":
    unittest.main()
