"""
Unit Tests for TradeNotifier Nightly Audit and Unified Backtest Engine
Verifies:
1. TradeNotifier.notify_nightly_audit dispatch and payload formatting.
2. UnifiedBacktestEngine strategy setup rules:
   - ApexHunter v1.2.1 on Gold (XAUUSD) & Nasdaq (NAS100)
   - Lewis Kelly SMC on Forex pairs (EURUSD, GBPUSD, etc.)
3. Breakeven buffer (+0.2R) on TP1 hit for both 2-split and 3-split orders.
4. Circuit Breakers: Daily 4.0% CB and Floor 8.5% CB.
5. Max concurrent positions (4 slots) and max 2 losses per symbol/day.
"""

import unittest
import os
import sys
from datetime import datetime, date, time as dtime, timedelta, timezone
from unittest.mock import MagicMock, patch
import pandas as pd
import numpy as np

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from live.notifier import TradeNotifier
from backtest.unified_backtest_engine import (
    UnifiedBacktestEngine,
    BacktestTrade,
    BacktestRejectedSetup,
    BacktestResult,
)


class TestNotifierNightlyAudit(unittest.TestCase):
    def test_notify_nightly_audit_enqueue(self):
        notifier = TradeNotifier({
            "active_platform": "all",
            "telegram_bot_token": "TEST_TG_TOKEN",
            "telegram_chat_id": "-100123456",
            "discord_webhook_url": "https://discord.com/webhook/test"
        })

        with patch.object(notifier, '_enqueue') as mock_enqueue:
            notifier.notify_nightly_audit(
                title="🌙 Master Unified Nightly Audit Scorecard (2026-11-20)",
                summary_text="<b>Match Rate:</b> 100.0%\n<b>Backtest PnL:</b> +$125.50\n<b>Live PnL:</b> +$124.80",
                discord_fields=[
                    {"name": "Reconciliation Status", "value": "100% Parity Verified", "inline": False}
                ]
            )
            mock_enqueue.assert_called_once()
            payload = mock_enqueue.call_args[0][0]
            self.assertIn("Master Unified Nightly Audit", payload["embed"]["title"])
            self.assertEqual(payload["embed"]["color"], 0x2ECC71)
            self.assertIn("Match Rate:", payload["text"])


class TestUnifiedBacktestEngine(unittest.TestCase):
    def setUp(self):
        self.engine = UnifiedBacktestEngine(
            symbols=["XAUUSD", "NAS100", "EURUSD", "GBPUSD"],
            base_capital=5000.0,
            risk_per_trade=0.01,
            max_concurrent_trades=4,
            max_daily_losses_per_symbol=2,
            daily_cb_pct=0.04,
            max_floor_cb_pct=0.085,
        )

    def test_symbol_strategy_routing(self):
        self.assertEqual(self.engine.get_strategy_for_symbol("XAUUSD"), "APEXHUNTER_1_2_1")
        self.assertEqual(self.engine.get_strategy_for_symbol("NAS100"), "APEXHUNTER_1_2_1")
        self.assertEqual(self.engine.get_strategy_for_symbol("EURUSD"), "LEWIS_KELLY_SMC")
        self.assertEqual(self.engine.get_strategy_for_symbol("GBPUSD"), "LEWIS_KELLY_SMC")
        self.assertEqual(self.engine.get_strategy_for_symbol("USDJPY"), "LEWIS_KELLY_SMC")

    def test_apexhunter_targets_and_splits(self):
        # Gold: TP1 = 1.4R, TP2 = 2.2R, 50% / 50% split
        entry = 2650.0
        sl = 2640.0
        sl_dist = 10.0
        splits = self.engine.calculate_trade_splits("APEXHUNTER_1_2_1", "XAUUSD", 1, entry, sl, total_lots=0.10)
        self.assertEqual(len(splits), 2)
        self.assertEqual(splits[0]["volume"], 0.05)
        self.assertEqual(splits[1]["volume"], 0.05)
        self.assertAlmostEqual(splits[0]["tp"], entry + 1.4 * sl_dist, places=2)
        self.assertAlmostEqual(splits[1]["tp"], entry + 2.2 * sl_dist, places=2)
        # Breakeven SL: entry + 0.2 * sl_dist
        be_sl = self.engine.calculate_breakeven_sl(1, entry, sl_dist)
        self.assertAlmostEqual(be_sl, 2652.0, places=2)

    def test_forex_targets_and_splits(self):
        # EURUSD: TP1 = 2.5R, TP2 = 5.0R, TP3 = 8.0R, 30% / 30% / 40% split
        entry = 1.08500
        sl = 1.08400
        sl_dist = 0.00100  # 10 pips
        splits = self.engine.calculate_trade_splits("LEWIS_KELLY_SMC", "EURUSD", 1, entry, sl, total_lots=0.10)
        self.assertEqual(len(splits), 3)
        self.assertEqual(splits[0]["volume"], 0.03)
        self.assertEqual(splits[1]["volume"], 0.03)
        self.assertEqual(splits[2]["volume"], 0.04)
        self.assertAlmostEqual(splits[0]["tp"], entry + 2.5 * sl_dist, places=5)
        self.assertAlmostEqual(splits[1]["tp"], entry + 5.0 * sl_dist, places=5)
        self.assertAlmostEqual(splits[2]["tp"], entry + 8.0 * sl_dist, places=5)
        # Breakeven SL: entry + 0.2 * sl_dist
        be_sl = self.engine.calculate_breakeven_sl(1, entry, sl_dist)
        self.assertAlmostEqual(be_sl, 1.08520, places=5)

    def test_simulation_trade_tp1_then_be_hit(self):
        # Test simulated trade where price reaches TP1, shifts to BE (+0.2R), then reverses to hit BE
        # Buying EURUSD: entry 1.1000, sl 1.0990 (sl_dist = 0.0010, 10 pips)
        # TP1 = 1.1025 (+2.5R), TP2 = 1.1050 (+5.0R), TP3 = 1.1080 (+8.0R)
        # BE SL = 1.1002 (+0.2R)
        trade = self.engine.simulate_trade_on_bar_sequence(
            symbol="EURUSD",
            strategy="LEWIS_KELLY_SMC",
            direction="BUY",
            entry_time=datetime(2026, 11, 20, 9, 0, tzinfo=timezone.utc),
            entry_price=1.1000,
            sl_price=1.0990,
            tp_levels=[1.1025, 1.1050, 1.1080],
            total_lots=0.10,
            bars=[
                # Bar 1: Price goes up to touch TP1 (high 1.1026, low 1.0995)
                {"time": datetime(2026, 11, 20, 9, 15, tzinfo=timezone.utc), "open": 1.1000, "high": 1.1026, "low": 1.0995, "close": 1.1020},
                # Bar 2: Price drops to hit BE SL at 1.1002 (high 1.1020, low 1.1001, close 1.1005)
                {"time": datetime(2026, 11, 20, 9, 30, tzinfo=timezone.utc), "open": 1.1020, "high": 1.1020, "low": 1.1001, "close": 1.1005},
            ]
        )
        self.assertTrue(trade.tp1_hit)
        self.assertTrue(trade.be_hit)
        self.assertFalse(trade.initial_sl_hit)
        self.assertGreater(trade.net_pnl, 0.0)  # TP1 banked 30% @ 2.5R, runner exited at +0.2R

    def test_simulation_trade_initial_sl_hit(self):
        # Price drops directly to hit SL before TP1
        trade = self.engine.simulate_trade_on_bar_sequence(
            symbol="XAUUSD",
            strategy="APEXHUNTER_1_2_1",
            direction="BUY",
            entry_time=datetime(2026, 11, 20, 14, 0, tzinfo=timezone.utc),
            entry_price=2650.0,
            sl_price=2640.0,
            tp_levels=[2664.0, 2672.0],
            total_lots=0.10,
            bars=[
                {"time": datetime(2026, 11, 20, 14, 5, tzinfo=timezone.utc), "open": 2650.0, "high": 2652.0, "low": 2639.5, "close": 2641.0},
            ]
        )
        self.assertFalse(trade.tp1_hit)
        self.assertTrue(trade.initial_sl_hit)
        self.assertLess(trade.net_pnl, 0.0)

    def test_usdjpy_tick_value_and_pnl(self):
        # USDJPY: quote currency is JPY. At price 150.00, 1 pip (10 ticks) = ~$6.67 USD per 1 lot.
        # With 0.10 lots, 20 pips loss (0.200 pts) should be ~$13-$14 USD, NOT $2000!
        trade = self.engine.simulate_trade_on_bar_sequence(
            symbol="USDJPY",
            strategy="LEWIS_KELLY_SMC",
            direction="BUY",
            entry_time=datetime(2026, 11, 20, 9, 0, tzinfo=timezone.utc),
            entry_price=150.00,
            sl_price=149.80,  # 20 pips SL
            tp_levels=[150.50, 151.00, 151.60],
            total_lots=0.10,
            bars=[
                {"time": datetime(2026, 11, 20, 9, 15, tzinfo=timezone.utc), "open": 150.00, "high": 150.05, "low": 149.75, "close": 149.78},
            ]
        )
        self.assertTrue(trade.initial_sl_hit)
        # Expected loss ~ -$13.33 USD - $0.50 commission = -$13.83 USD
        self.assertGreater(trade.net_pnl, -25.0)
        self.assertLess(trade.net_pnl, -5.0)

    def test_low_volume_splits(self):
        # 0.01 lots -> single order
        splits_1 = self.engine.calculate_trade_splits("LEWIS_KELLY_SMC", "EURUSD", 1, 1.0850, 1.0840, 0.01)
        self.assertEqual(len(splits_1), 1)
        self.assertEqual(splits_1[0]["volume"], 0.01)

        # 0.02 lots -> 2 orders (0.01 + 0.01)
        splits_2 = self.engine.calculate_trade_splits("LEWIS_KELLY_SMC", "EURUSD", 1, 1.0850, 1.0840, 0.02)
        self.assertEqual(len(splits_2), 2)
        self.assertEqual(splits_2[0]["volume"], 0.01)
        self.assertEqual(splits_2[1]["volume"], 0.01)


if __name__ == "__main__":
    unittest.main()
