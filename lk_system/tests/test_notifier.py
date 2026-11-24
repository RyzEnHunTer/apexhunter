import unittest
import os
import sys
import time
from unittest.mock import patch, MagicMock
from datetime import datetime, timezone

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from live.notifier import (
    TradeNotifier,
    NotificationManager,
    format_dual_time,
    format_dual_hhmm,
    format_dollar,
)


class TestNotifier(unittest.TestCase):
    def test_dynamic_config_update(self):
        notifier = TradeNotifier({
            "active_platform": "telegram",
            "telegram_bot_token": "TEST_TOKEN_123",
            "telegram_chat_id": "-100123456",
            "discord_webhook_url": "https://discord.com/webhook/test"
        })
        self.assertEqual(notifier.active_platform, "telegram")
        self.assertEqual(notifier.tg_token, "TEST_TOKEN_123")
        self.assertEqual(notifier.tg_chat_id, "-100123456")
        self.assertEqual(notifier.discord_url, "https://discord.com/webhook/test")

        # Update to Discord only
        notifier.update_config({"active_platform": "discord"})
        self.assertEqual(notifier.active_platform, "discord")

        # Update to None (Mute)
        notifier.update_config({"active_platform": "none"})
        self.assertEqual(notifier.active_platform, "none")

        # Update to All (Dual dispatch)
        notifier.update_config({"active_platform": "all"})
        self.assertEqual(notifier.active_platform, "all")

    def test_test_connection_validation(self):
        ok, msg = TradeNotifier.test_connection("telegram", "", "")
        self.assertFalse(ok)
        self.assertIn("empty", msg.lower())

        ok, msg = TradeNotifier.test_connection("discord", "")
        self.assertFalse(ok)
        self.assertIn("empty", msg.lower())

        ok, msg = TradeNotifier.test_connection("unknown_platform", "something")
        self.assertFalse(ok)
        self.assertIn("unknown", msg.lower())

    def test_compatibility_alias(self):
        self.assertIs(NotificationManager, TradeNotifier)

    def test_dual_time_formatter(self):
        formatted = format_dual_time()
        self.assertIn("UTC", formatted)
        self.assertIn("IST", formatted)

        # Naive datetime
        naive_dt = datetime(2026, 11, 20, 14, 30, 0)
        formatted_naive = format_dual_time(naive_dt, include_date=True)
        self.assertIn("2026-11-20 14:30:00 UTC", formatted_naive)
        self.assertIn("20:00:00 IST", formatted_naive)

        # Without date
        time_only = format_dual_time(naive_dt, include_date=False)
        self.assertEqual(time_only, "14:30:00 UTC (20:00:00 IST)")

    def test_dual_hhmm_formatter(self):
        res = format_dual_hhmm("10:00 UTC")
        self.assertIn("10:00 UTC", res)
        self.assertIn("15:30 IST", res)

        # Already has IST
        res_cached = format_dual_hhmm("06:00 UTC (11:30 IST)")
        self.assertEqual(res_cached, "06:00 UTC (11:30 IST)")

    def test_dollar_formatter(self):
        self.assertEqual(format_dollar(125.50), "+$125.50")
        self.assertEqual(format_dollar(-45.25), "-$45.25")
        self.assertEqual(format_dollar(0.0), "+$0.00")
        self.assertEqual(format_dollar(50.0, force_sign=False), "$50.00")

    @patch.object(TradeNotifier, "_send_telegram_http")
    @patch.object(TradeNotifier, "_send_discord_http")
    def test_all_notification_methods_enqueue_and_dispatch(self, mock_discord, mock_tg):
        mock_discord.return_value = (True, "OK")
        mock_tg.return_value = (True, "OK")

        notifier = TradeNotifier({
            "active_platform": "all",
            "telegram_bot_token": "TOK",
            "telegram_chat_id": "CHAT",
            "discord_webhook_url": "https://discord.com/api/webhooks/test"
        })

        # 1. notify_startup
        notifier.notify_startup(
            account_id=1234567,
            server="MetaQuotes-Demo",
            mode="live",
            equity=5000.0,
            balance=5000.0,
            risk_pct=1.0,
            daily_dd=4.0,
            max_dd=8.5,
            symbols=["XAUUSD", "NAS100", "EURUSD"],
            daily_cb=4.0,
            max_cb=8.5,
            strategy_desc="Simultaneous Dual-Engine (ApexHunter + LK SMC)"
        )

        # 2. notify_shutdown
        notifier.notify_shutdown(reason="Graceful Shutdown")

        # 3. notify_session
        notifier.notify_session("London Session", "OPENED")

        # 4. notify_trading_paused & resumed
        notifier.notify_trading_paused("Morning Dead Trap Hour", "Lull chop", "10:00 UTC", is_startup=False)
        notifier.notify_trading_resumed("Morning Trap Ended", "Active London Window")

        # 5. notify_trade_opened (Apex twin-split)
        notifier.notify_trade_opened(
            symbol="XAUUSD",
            direction="BUY",
            entry_price=2750.50,
            spread=0.25,
            total_lots=0.20,
            partial_lots=0.10,
            runner_lots=0.10,
            sl=2740.00,
            tp1=2765.00,
            tp2=2780.00,
            ticket_a=1001,
            ticket_b=1002,
            strategy="ApexHunter"
        )

        # 6. notify_trade_opened (SMC 3-split)
        notifier.notify_trade_opened(
            symbol="EURUSD",
            direction="SELL",
            entry_price=1.05500,
            spread=0.15,
            total_lots=0.30,
            sl=1.05800,
            splits=[
                {"label": "Order 1", "volume": 0.09, "tp": 1.04750},
                {"label": "Order 2", "volume": 0.09, "tp": 1.04000},
                {"label": "Order 3", "volume": 0.12, "tp": 1.03100},
            ],
            tickets=[2001, 2002, 2003],
            strategy="Lewis Kelly SMC"
        )

        # 7. notify_tp1_breakeven
        notifier.notify_tp1_breakeven(
            symbol="XAUUSD",
            ticket_a=1001,
            ticket_b=1002,
            be_sl=2750.75,
            profit_a=50.0,
            strategy="ApexHunter"
        )

        # 8. notify_tp1_breakeven with sibling list
        notifier.notify_tp1_breakeven(
            symbol="EURUSD",
            ticket_a=2001,
            ticket_b=[2002, 2003],
            be_sl=1.05480,
            profit_a=35.0,
            strategy="Lewis Kelly SMC"
        )

        # 9. notify_trade_closed
        notifier.notify_trade_closed(
            symbol="XAUUSD",
            ticket=1002,
            exit_reason="Runner TP2 Hit",
            pnl=120.0,
            ticket_a=1001,
            pnl_a=50.0,
            pnl_b=70.0,
            day_pnl=120.0,
            day_pnl_pct=2.40,
            daily_dd_pct=0.0,
            remaining_cushion=200.0,
            daily_cb_pct=4.0
        )

        # 10. notify_daily_summary
        notifier.notify_daily_summary(
            date_str="2026-11-20",
            starting_equity=5000.0,
            closing_equity=5120.0,
            balance=5120.0,
            trades_count=2,
            winning_trades=2,
            losing_trades=0,
            daily_pnl=120.0,
            daily_pnl_pct=2.40,
            daily_dd_pct=0.0,
            cushion_remaining=200.0,
            closed_trades_details=[
                {"ticket": 1001, "symbol": "XAUUSD", "type": "BUY", "volume": 0.1, "profit": 50.0, "comment": "TP1"},
                {"ticket": 1002, "symbol": "XAUUSD", "type": "BUY", "volume": 0.1, "profit": 70.0, "comment": "TP2"}
            ]
        )

        # 11. notify_circuit_breaker
        notifier.notify_circuit_breaker("daily", 4850.0, 4.0, 150.0, 5.0)
        notifier.notify_circuit_breaker("floor", 4570.0, 8.5, 430.0, 10.0)

        # 12. notify_news_shield
        notifier.notify_news_shield_activated("US Non-Farm Payrolls", "USD", "13:30 UTC", "13:45 UTC")
        notifier.notify_news_shield_lifted("US Non-Farm Payrolls", "13:45 UTC")

        # 13. notify_weekend_close
        notifier.notify_weekend_close(
            closed_trades=[{"symbol": "GBPUSD", "ticket": 3001, "volume": 0.1, "pnl": 15.0}],
            total_pnl=15.0,
            resume_time_str="Monday 06:00 UTC (11:30 IST)"
        )

        # 14. notify_challenge_passed & phase transition
        notifier.notify_challenge_passed("1234567", 1, 8.0, 5420.0, 420.0, 8.40, is_final=False)
        notifier.notify_challenge_passed("1234567", 2, 5.0, 5680.0, 260.0, 5.20, is_final=True)
        notifier.notify_phase_transition("1234567", "Phase 2", 5420.0, 5.0, 1.0, is_custom_risk_on=False)

        # 15. notify_recovery
        notifier.notify_recovery(
            recovered_count=1,
            recovered_positions=[{
                "symbol": "XAUUSD",
                "direction": "BUY",
                "entry_price": 2650.0,
                "sl": 2652.0,
                "tp1_hit": True,
                "tickets": [101, 102]
            }],
            risk_restored=True,
            day_start_balance=5100.0,
            account_id=1234567,
            server="MetaQuotes-Demo"
        )

        # Wait for queue worker to process items
        time.sleep(0.6)

        # Verify that both Telegram and Discord received notifications
        self.assertGreater(mock_tg.call_count, 10)
        self.assertGreater(mock_discord.call_count, 10)


if __name__ == "__main__":
    unittest.main()
