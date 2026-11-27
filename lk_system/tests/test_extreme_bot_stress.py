"""
Extreme Adversarial Stress Tests for MasterUnifiedBot & Core Engines
====================================================================
Verifies resilience against production live-trading failure modes:
1. Close liquidation filling mode & state preservation on failed closes
2. Broker UTC+offset midnight restart persistence (22:00-23:59 UTC gap)
3. CLI override precedence, daily_drawdown_floor recalculation, & WinError 233 in initialize()
4. Partial split fills, transient requote retries, SL-cross abort, & unique dry-run tickets
5. JPY / XAU rounding tolerance in crash-recovery Breakeven detection
6. 200-cycle chaos Monte Carlo (IPC None returns, duplicate/NaN candles, Weekend/News/CB toggles)
"""

import os
import random
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd

import sys
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from core.mt5_live_bridge import MT5LiveBridge
from core.order_manager import OrderManager
from core.persistence_manager import BotPersistenceManager
from core.risk_guardian import RiskGuardian
from core.lewis_kelly_engine import LewisKellySMCEngine


class TestExtremeBotStress(unittest.TestCase):
    def test_01_close_position_sends_filling_mode_and_preserves_state_on_failure(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("master_live_bot.mt5") as mock_mt5:
                from master_live_bot import MasterUnifiedBot

                bot = MasterUnifiedBot(dry_run=False, base_capital=5000.0)
                bot.persistence_manager = BotPersistenceManager(
                    state_file_path=os.path.join(tmpdir, "state.json")
                )
                bot.bridge = MagicMock()
                bot.bridge.resolved_symbols = {"EURUSD": "EURUSD.r"}
                bot.bridge.broker_offset = timedelta(hours=2)

                pos_entry = {
                    "group_id": "EURUSD_100",
                    "strategy": "LEWIS_KELLY_SMC",
                    "symbol": "EURUSD",
                    "broker_sym": "EURUSD.r",
                    "direction": "SELL",
                    "entry_price": 1.0950,
                    "sl": 1.0965,
                    "be_sl": 1.0947,
                    "splits": [{"label": "SMC_TP1_2.5R", "volume": 0.03, "tp": 1.0912}],
                    "tickets": [7001],
                    "tp1_hit": False,
                }
                bot.open_positions["EURUSD"] = pos_entry
                bot.persistence_manager.save_position("EURUSD_100", pos_entry)

                mock_mt5.ORDER_TYPE_BUY = 0
                mock_mt5.ORDER_TYPE_SELL = 1
                mock_mt5.TRADE_ACTION_DEAL = 1
                mock_mt5.ORDER_TIME_GTC = 0
                mock_mt5.ORDER_FILLING_IOC = 1
                mock_mt5.ORDER_FILLING_FOK = 0
                mock_mt5.ORDER_FILLING_RETURN = 2
                mock_mt5.TRADE_RETCODE_DONE = 10009
                mock_mt5.positions_get.return_value = [
                    SimpleNamespace(ticket=7001, symbol="EURUSD.r", type=1, volume=0.03, price_current=1.0955)
                ]
                # Require IOC (modes & 2 != 0, modes & 1 == 0)
                mock_mt5.symbol_info.return_value = SimpleNamespace(filling_mode=2)
                mock_mt5.symbol_info_tick.return_value = SimpleNamespace(bid=1.0954, ask=1.0956)

                # First attempt fails -> position MUST stay tracked in memory & disk!
                mock_mt5.order_send.return_value = SimpleNamespace(retcode=10030, comment="Unsupported filling mode")
                bot.close_position_by_symbol("EURUSD", reason="News Auto-Close")

                req_sent = mock_mt5.order_send.call_args[0][0]
                self.assertEqual(req_sent["type_filling"], mock_mt5.ORDER_FILLING_IOC)
                self.assertEqual(req_sent["type_time"], mock_mt5.ORDER_TIME_GTC)
                self.assertIn("EURUSD", bot.open_positions)
                self.assertIsNotNone(bot.persistence_manager.get_position("EURUSD_100"))

                # Second attempt succeeds -> position cleanly removed
                mock_mt5.order_send.return_value = SimpleNamespace(retcode=10009, comment="Done")
                bot.close_position_by_symbol("EURUSD", reason="News Auto-Close")
                self.assertNotIn("EURUSD", bot.open_positions)
                self.assertIsNone(bot.persistence_manager.get_position("EURUSD_100"))

    @patch("master_live_bot.datetime")
    @patch("master_live_bot.console")
    @patch("master_live_bot.mt5")
    def test_02_broker_midnight_restart_preserves_daily_cb_and_loss_counts(
        self, mock_mt5, mock_console, mock_dt
    ):
        from master_live_bot import MasterUnifiedBot

        mock_console.print.side_effect = OSError(233, "Pipe broken")
        # 22:30 UTC on Nov 27 -> with broker UTC+2, broker date is ALREADY Nov 28!
        fixed_utc = datetime(2026, 11, 27, 22, 30, tzinfo=timezone.utc)
        mock_dt.now.side_effect = lambda tz=None: fixed_utc
        mock_dt.strptime = datetime.strptime

        with tempfile.TemporaryDirectory() as tmpdir:
            state_path = os.path.join(tmpdir, "runtime_state.json")

            # Bot 1 hits Daily CB + 2 EURUSD losses at 22:25 UTC (broker date 2026-11-28)
            bot1 = MasterUnifiedBot(dry_run=False, base_capital=5000.0)
            bot1.persistence_manager = BotPersistenceManager(state_file_path=state_path)
            bot1.bridge = MagicMock()
            bot1.bridge.broker_offset = timedelta(hours=2)
            bot1.risk_guardian.reset_daily_if_new_day(fixed_utc + timedelta(hours=2), 5000.0, 5000.0)
            bot1.risk_guardian.daily_halt_active = True
            bot1.risk_guardian.record_trade_result("EURUSD", is_loss=True)
            bot1.risk_guardian.record_trade_result("EURUSD", is_loss=True)
            bot1._sync_risk_persistence()

            # Bot 2 restarts at 22:30 UTC (still Nov 27 UTC, Nov 28 broker server time)
            bot2 = MasterUnifiedBot(dry_run=False, base_capital=5000.0)
            bot2.persistence_manager = BotPersistenceManager(state_file_path=state_path)
            bot2.acc_mgr = MagicMock()
            bot2.acc_mgr.get_or_setup_account.return_value = {
                "account_lifecycle": "challenge",
                "current_phase": 1,
                "daily_cb_pct": 4.0,
                "max_cb_pct": 8.5,
                "max_total_dd_pct": 10.0,
                "risk_per_trade": 0.01,
                "use_custom_phase_risk": False,
                "target_locked": False,
            }
            bot2.bridge = MagicMock()
            bot2.bridge.connect.return_value = True
            bot2.bridge.broker_offset = timedelta(hours=2)
            bot2.notifier = MagicMock()

            mock_mt5.account_info.return_value = SimpleNamespace(
                login=213877054, server="OctaFX-Demo", balance=4800.0, equity=4800.0, margin_free=4800.0
            )
            mock_mt5.terminal_info.return_value = SimpleNamespace(company="Octa", name="MT5")
            mock_mt5.positions_get.return_value = []

            self.assertTrue(bot2.initialize())
            self.assertTrue(bot2.risk_guardian.daily_halt_active)
            self.assertTrue(bot2.risk_guardian.symbol_daily_halted.get("EURUSD"))
            self.assertEqual(bot2.risk_guardian.day_start_balance, 5000.0)

    @patch("master_live_bot.console")
    @patch("master_live_bot.mt5")
    def test_03_initialize_cli_overrides_and_floor_recalculation(self, mock_mt5, mock_console):
        from master_live_bot import MasterUnifiedBot

        mock_console.print.side_effect = OSError(233, "Pipe broken")
        with tempfile.TemporaryDirectory() as tmpdir:
            bot = MasterUnifiedBot(
                dry_run=True,
                base_capital=5000.0,
                daily_cb_pct=4.5,
                max_floor_cb_pct=8.5,
                hard_floor_pct=10.0,
                risk_pct=1.25,
            )
            bot.persistence_manager = BotPersistenceManager(
                state_file_path=os.path.join(tmpdir, "state.json")
            )
            bot.acc_mgr = MagicMock()
            # Profile has DIFFERENT lower limits (e.g. 3.0% CB / 1.0% risk) -> CLI must win!
            bot.acc_mgr.get_or_setup_account.return_value = {
                "account_lifecycle": "challenge",
                "current_phase": 1,
                "daily_cb_pct": 3.0,
                "max_cb_pct": 7.0,
                "max_total_dd_pct": 8.5,
                "risk_per_trade": 0.01,
                "use_custom_phase_risk": True,
                "target_locked": False,
            }
            bot.bridge = MagicMock()
            bot.bridge.connect.return_value = True
            bot.bridge.broker_offset = timedelta(hours=2)
            bot.notifier = MagicMock()
            mock_mt5.account_info.return_value = SimpleNamespace(
                login=213877054, server="OctaFX-Demo", balance=5000.0, equity=5000.0
            )
            mock_mt5.terminal_info.return_value = SimpleNamespace(company="Octa", name="MT5")

            self.assertTrue(bot.initialize())
            self.assertAlmostEqual(bot.risk_guardian.daily_cb_pct, 0.045, places=6)
            self.assertAlmostEqual(bot.risk_guardian.max_floor_cb_pct, 0.085, places=6)
            self.assertAlmostEqual(bot.risk_guardian.hard_floor_pct, 0.100, places=6)
            self.assertAlmostEqual(bot.risk_guardian.normal_risk_pct, 0.0125, places=6)
            self.assertAlmostEqual(bot.risk_guardian.daily_drawdown_floor, 4775.0, places=2)

            # Without CLI overrides, acc_cfg 3.0% must also recalculate daily_drawdown_floor to 4850.0!
            bot_no_cli = MasterUnifiedBot(dry_run=True, base_capital=5000.0)
            bot_no_cli.persistence_manager = BotPersistenceManager(
                state_file_path=os.path.join(tmpdir, "state2.json")
            )
            bot_no_cli.acc_mgr = bot.acc_mgr
            bot_no_cli.bridge = bot.bridge
            bot_no_cli.notifier = MagicMock()
            self.assertTrue(bot_no_cli.initialize())
            self.assertAlmostEqual(bot_no_cli.risk_guardian.daily_cb_pct, 0.030, places=6)
            self.assertAlmostEqual(bot_no_cli.risk_guardian.daily_drawdown_floor, 4850.0, places=2)

    @patch("master_live_bot.mt5")
    def test_04_partial_split_fill_requote_retry_and_unique_dry_tickets(self, mock_mt5):
        from core.apexhunter_engine import SignalType
        from master_live_bot import MasterUnifiedBot

        with tempfile.TemporaryDirectory() as tmpdir:
            bot = MasterUnifiedBot(dry_run=False, base_capital=5000.0)
            bot.persistence_manager = BotPersistenceManager(
                state_file_path=os.path.join(tmpdir, "state.json")
            )
            bot.notifier = MagicMock()
            bot.bridge = MagicMock()
            bot.bridge.broker_offset = timedelta(hours=2)
            bot.bridge.get_specs.return_value = {
                "digits": 5, "tick_size": 0.00001, "tick_value": 1.0,
                "volume_min": 0.01, "volume_step": 0.01, "volume_max": 50.0,
            }
            bot.bridge.get_latest_quote.return_value = {"spread": 8}

            mock_mt5.ORDER_TYPE_BUY = 0
            mock_mt5.ORDER_TYPE_SELL = 1
            mock_mt5.TRADE_ACTION_DEAL = 1
            mock_mt5.ORDER_TIME_GTC = 0
            mock_mt5.ORDER_FILLING_FOK = 0
            mock_mt5.TRADE_RETCODE_DONE = 10009
            mock_mt5.symbol_info.return_value = SimpleNamespace(filling_mode=1)
            mock_mt5.symbol_info_tick.return_value = SimpleNamespace(ask=1.0951, bid=1.0950)

            # T1 requote -> retry DONE (#501); T2 fails permanently (10016); T3 DONE (#503)
            mock_mt5.order_send.side_effect = [
                SimpleNamespace(retcode=10004, comment="Requote", order=0),
                SimpleNamespace(retcode=10009, comment="Done", order=501),
                SimpleNamespace(retcode=10016, comment="Invalid stops", order=0),
                SimpleNamespace(retcode=10009, comment="Done", order=503),
            ]

            bot.execute_order(
                strategy="LEWIS_KELLY_SMC",
                symbol="EURUSD",
                broker_sym="EURUSD",
                signal_type=SignalType.SELL,
                entry_price=1.0950,
                stop_loss=1.0960,
                tp_levels=[1.0925, 1.0900, 1.0870],
                risk_usd=50.0,
            )
            self.assertEqual(bot.open_positions["EURUSD"]["tickets"], [501, 503])
            self.assertEqual(len(bot.open_positions["EURUSD"]["splits"]), 2)

            # Live price beyond SL must abort cleanly without sending orders
            mock_mt5.order_send.reset_mock()
            mock_mt5.symbol_info_tick.return_value = SimpleNamespace(ask=1.0970, bid=1.0965)
            bot.execute_order(
                strategy="LEWIS_KELLY_SMC",
                symbol="GBPUSD",
                broker_sym="GBPUSD",
                signal_type=SignalType.SELL,
                entry_price=1.0950,
                stop_loss=1.0960,
                tp_levels=[1.0925, 1.0900, 1.0870],
                risk_usd=50.0,
            )
            mock_mt5.order_send.assert_not_called()

            # Dry-run monotonic ticket uniqueness even across multiple trades
            bot.dry_run = True
            mock_mt5.symbol_info_tick.return_value = SimpleNamespace(ask=1.0951, bid=1.0950)
            bot.execute_order("LEWIS_KELLY_SMC", "EURUSD", "EURUSD", SignalType.SELL, 1.0950, 1.0960, [1.0925, 1.0900, 1.0870], 50.0)
            t_set_1 = set(bot.open_positions["EURUSD"]["tickets"])
            bot.open_positions.clear()
            bot.execute_order("LEWIS_KELLY_SMC", "EURUSD", "EURUSD", SignalType.SELL, 1.0950, 1.0960, [1.0925, 1.0900, 1.0870], 50.0)
            t_set_2 = set(bot.open_positions["EURUSD"]["tickets"])
            self.assertTrue(t_set_1.isdisjoint(t_set_2))

    def test_05_jpy_and_xau_crash_recovery_be_tolerance(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            pm = BotPersistenceManager(state_file_path=os.path.join(tmpdir, "state.json"))
            om = OrderManager()
            rg = RiskGuardian()

            # Save USDJPY (be_sl has 4th decimal .3454, MT5 rounds SL to 3 decimals 152.345)
            pm.save_position("USDJPY_1", {
                "group_id": "USDJPY_1",
                "strategy": "LEWIS_KELLY_SMC",
                "symbol": "USDJPY",
                "direction": "BUY",
                "entry_price": 152.300,
                "sl": 152.3454,
                "be_sl": 152.3454,
                "tp1_hit": True,
                "tickets": [8001, 8002, 8003],
            })
            # Save XAUUSD (be_sl 2650.124, MT5 rounded SL 2650.12)
            pm.save_position("XAUUSD_1", {
                "group_id": "XAUUSD_1",
                "strategy": "APEXHUNTER_1_2_1",
                "symbol": "XAUUSD",
                "direction": "BUY",
                "entry_price": 2650.00,
                "sl": 2650.124,
                "be_sl": 2650.124,
                "tp1_hit": True,
                "tickets": [9001, 9002],
            })

            live_pos = [
                SimpleNamespace(ticket=8002, symbol="USDJPY", type=0, sl=152.345, magic=88002, price_open=152.300),
                SimpleNamespace(ticket=8003, symbol="USDJPY", type=0, sl=152.345, magic=88003, price_open=152.300),
                SimpleNamespace(ticket=9002, symbol="XAUUSD", type=0, sl=2650.12, magic=88002, price_open=2650.00),
            ]
            pm.recover_state(live_pos, om, rg)
            self.assertTrue(om.active_groups["USDJPY_1"]["be_updated"])
            self.assertTrue(om.active_groups["XAUUSD_1"]["be_updated"])

    @patch("master_live_bot.console")
    @patch("master_live_bot.mt5")
    def test_06_chaos_200_cycles_and_order_splits_invariants(self, mock_mt5, mock_console):
        from master_live_bot import MasterUnifiedBot

        # A) Property-based split fuzzing (50 combinations)
        om = OrderManager()
        rng = random.Random(42)
        for _ in range(50):
            v_min, v_step = rng.choice([(0.01, 0.01), (0.02, 0.01), (0.05, 0.05), (0.10, 0.10)])
            tot = round(rng.uniform(v_min, 5.0) / v_step) * v_step
            for strat in ("LEWIS_KELLY_SMC", "APEXHUNTER_1_2_1"):
                splits = om.calculate_order_splits(strat, tot, [1.10, 1.12, 1.15], v_min, v_step)
                self.assertGreaterEqual(len(splits), 1)
                for sp in splits:
                    self.assertGreaterEqual(sp["volume"], v_min - 1e-9)

        # B) 200-cycle Chaos run_cycle() using spec=MT5LiveBridge (triggers real daily rollover + _sync_broker_timezone)
        mock_console.print.side_effect = OSError(233, "Detached pipe")
        with tempfile.TemporaryDirectory() as tmpdir:
            bot = MasterUnifiedBot(dry_run=True, base_capital=5000.0)
            bot.persistence_manager = BotPersistenceManager(
                state_file_path=os.path.join(tmpdir, "chaos.json")
            )
            bot.notifier = MagicMock()
            bot.bridge = MagicMock(spec=MT5LiveBridge)
            bot.bridge.broker_offset = timedelta(hours=2)
            bot.bridge.resolved_symbols = {"XAUUSD": "XAUUSD", "EURUSD": "EURUSD"}
            bot.bridge.raw_targets = ["XAUUSD", "EURUSD"]
            bot.open_positions = {
                "EURUSD": {
                    "group_id": "G1", "strategy": "LEWIS_KELLY_SMC", "symbol": "EURUSD",
                    "direction": "BUY", "entry_price": 1.09, "sl": 1.08, "be_sl": 1.092,
                    "splits": [{"label": "TP1", "volume": 0.02, "tp": 1.12}],
                    "active_splits": [], "tickets": [101], "tp1_hit": False,
                }
            }

            for step in range(200):
                bal = 5000.0 - (step % 7) * 40.0
                if step % 9 == 0:
                    mock_mt5.account_info.return_value = None
                    bot.bridge.get_latest_quote.return_value = None
                else:
                    mock_mt5.account_info.return_value = SimpleNamespace(
                        balance=bal, equity=bal - (step % 3) * 15.0, margin_free=bal, login=213877054, server="Octa"
                    )
                    bot.bridge.get_latest_quote.return_value = {"bid": 1.091, "ask": 1.0912, "spread": 2}
                bot.bridge.check_new_bar_closed.return_value = (False, None)
                bot.run_cycle()

            bot.bridge._sync_broker_timezone.assert_called()
            saved = bot.persistence_manager.load_state()
            self.assertIsNotNone(saved["daily_risk_state"])

            # C) Verify MT5LiveBridge.fetch_rates deduplicates duplicate bar timestamps & sorts ascending
            with patch("core.mt5_live_bridge.mt5") as mock_bridge_mt5:
                real_bridge = MT5LiveBridge(target_symbols=["EURUSD"])
                real_bridge.resolved_symbols = {"EURUSD": "EURUSD"}
                dup_rates = [
                    {"time": 1764058500, "open": 1.09, "high": 1.091, "low": 1.089, "close": 1.0905, "tick_volume": 10},
                    {"time": 1764058200, "open": 1.089, "high": 1.09, "low": 1.088, "close": 1.0895, "tick_volume": 8},
                    {"time": 1764058500, "open": 1.09, "high": 1.092, "low": 1.089, "close": 1.0910, "tick_volume": 15},
                ]
                mock_bridge_mt5.copy_rates_from_pos.return_value = dup_rates
                df_dedup = real_bridge.fetch_rates("EURUSD", "M5", count=3)
                self.assertEqual(len(df_dedup), 2)
                self.assertTrue(df_dedup.index.is_monotonic_increasing)
                self.assertAlmostEqual(float(df_dedup["close"].iloc[-1]), 1.0910, places=5)

            # D) Ensure duplicate bar check & NaN-safe evaluation on LewisKellySMCEngine
            eng = LewisKellySMCEngine("USDJPY")
            base_utc = pd.date_range("2026-11-24 00:00", periods=40, freq="4h", tz="UTC")
            df_h4 = pd.DataFrame({
                "open": 152.0, "high": 152.5, "low": 151.5, "close": 152.1, "volume": 100
            }, index=base_utc)
            df_m15 = pd.DataFrame({
                "open": 152.0, "high": 152.2, "low": 151.9, "close": 152.0, "volume": 50
            }, index=pd.date_range("2026-11-24 00:00", periods=120, freq="15min", tz="UTC"))
            df_m1 = pd.DataFrame({
                "open": 152.0, "high": 152.05, "low": 151.95, "close": 152.0, "volume": 10
            }, index=pd.date_range("2026-11-25 07:00", periods=60, freq="1min", tz="UTC"))
            df_m1.iloc[-2, df_m1.columns.get_loc("close")] = np.nan

            _, d1 = eng.evaluate(df_h4, df_m15, df_m1)
            _, d2 = eng.evaluate(df_h4, df_m15, df_m1)
            self.assertNotEqual(d1["decision"], "REJECT_DUPLICATE_BAR")
            self.assertEqual(d2["decision"], "REJECT_DUPLICATE_BAR")


if __name__ == "__main__":
    unittest.main()
