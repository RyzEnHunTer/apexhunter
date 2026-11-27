"""
TDD Verification Suite for:
1. Authentic LewisKellySMCEngine (H4 Bias + M15 Trend + DealingRange + ADR_5 + Killzones + LTF CHoCH/FVG)
2. master_live_bot.py critical runtime bug fixes:
   - trade_stops_level (no AttributeError on SymbolInfo.stops_level)
   - d.position_id + entry==1 matching for closed deal PnL & consecutive loss tracking
   - Stale bar rejection (prevents zombie trades after bot restart/freeze)
   - Circuit breaker notification without RiskGuardian.max_floor_cb_breached AttributeError
   - Safe console printing against OSError (WinError 233)
"""

import os
import sys
import unittest
from datetime import datetime, timezone, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)


class TestLewisKellySMCEngineAndLiveFixes(unittest.TestCase):
    def test_01_lewis_kelly_engine_enforces_usdcad_ny_only_and_htf_filters(self):
        from core.lewis_kelly_engine import LewisKellySMCEngine, PAIRS_CONFIG

        self.assertEqual(PAIRS_CONFIG["USDCAD"]["target_sessions"], ["NY"])
        self.assertEqual(PAIRS_CONFIG["EURUSD"]["tf_exec"], "M1")
        self.assertEqual(PAIRS_CONFIG["GBPUSD"]["tf_exec"], "M5")

        eng = LewisKellySMCEngine("USDCAD")
        # Construct minimal H4/M15/M5 bars during London session (08:30 UTC / 03:30 NY)
        idx_h4 = pd.date_range("2026-11-18 00:00", periods=40, freq="4h", tz="UTC")
        df_h4 = pd.DataFrame({
            "open": np.linspace(1.42, 1.43, len(idx_h4)),
            "high": np.linspace(1.422, 1.432, len(idx_h4)),
            "low": np.linspace(1.418, 1.428, len(idx_h4)),
            "close": np.linspace(1.421, 1.431, len(idx_h4)),
            "volume": np.full(len(idx_h4), 1000),
        }, index=idx_h4)

        idx_m15 = pd.date_range("2026-11-20 00:00", "2026-11-25 08:30", freq="15min", tz="UTC")
        df_m15 = pd.DataFrame({
            "open": np.full(len(idx_m15), 1.425),
            "high": np.full(len(idx_m15), 1.427),
            "low": np.full(len(idx_m15), 1.423),
            "close": np.full(len(idx_m15), 1.425),
            "volume": np.full(len(idx_m15), 200),
        }, index=idx_m15)

        idx_m5 = pd.date_range("2026-11-25 06:00", "2026-11-25 08:30", freq="5min", tz="UTC")
        df_m5 = pd.DataFrame({
            "open": np.full(len(idx_m5), 1.425),
            "high": np.full(len(idx_m5), 1.426),
            "low": np.full(len(idx_m5), 1.424),
            "close": np.full(len(idx_m5), 1.425),
            "volume": np.full(len(idx_m5), 100),
        }, index=idx_m5)

        sig, diag = eng.evaluate(df_h4, df_m15, df_m5)
        self.assertIsNone(sig)
        self.assertIn("SESSION", diag["decision"])

    def test_02_lewis_kelly_engine_blocks_naked_asian_sweep_without_htf_alignment(self):
        """
        Proves a naked M15 Asian High wick sweep during London does NOT trigger a trade
        when H4 trend is bullish and there is no M15 bearish alignment / LTF CHoCH.
        """
        from core.lewis_kelly_engine import LewisKellySMCEngine

        eng = LewisKellySMCEngine("EURUSD")
        # Strong bullish H4 trend
        idx_h4 = pd.date_range("2026-11-15 00:00", periods=50, freq="4h", tz="UTC")
        closes_h4 = np.linspace(1.0800, 1.1050, len(idx_h4))
        df_h4 = pd.DataFrame({
            "open": closes_h4 - 0.0005,
            "high": closes_h4 + 0.0008,
            "low": closes_h4 - 0.0008,
            "close": closes_h4,
            "volume": np.full(len(idx_h4), 1000),
        }, index=idx_h4)

        # Bullish M15 with Asian range + single London bar poking above Asian High and closing inside
        idx_m15 = pd.date_range("2026-11-20 00:00", "2026-11-25 08:30", freq="15min", tz="UTC")
        closes_m15 = np.linspace(1.0900, 1.0960, len(idx_m15))
        highs_m15 = closes_m15 + 0.0003
        lows_m15 = closes_m15 - 0.0003
        df_m15 = pd.DataFrame({
            "open": closes_m15 - 0.0001,
            "high": highs_m15,
            "low": lows_m15,
            "close": closes_m15,
            "volume": np.full(len(idx_m15), 200),
        }, index=idx_m15)
        # Force last M15 bar to look like a naked Asian High sweep
        asia_h = df_m15.iloc[-10:-2]["high"].max()
        df_m15.iloc[-1, df_m15.columns.get_loc("high")] = asia_h + 0.0005
        df_m15.iloc[-1, df_m15.columns.get_loc("close")] = asia_h - 0.0002

        idx_m1 = pd.date_range("2026-11-25 07:00", "2026-11-25 08:44", freq="1min", tz="UTC")
        df_m1 = pd.DataFrame({
            "open": np.full(len(idx_m1), asia_h - 0.0002),
            "high": np.full(len(idx_m1), asia_h),
            "low": np.full(len(idx_m1), asia_h - 0.0003),
            "close": np.full(len(idx_m1), asia_h - 0.0001),
            "volume": np.full(len(idx_m1), 50),
        }, index=idx_m1)

        sig, diag = eng.evaluate(df_h4, df_m15, df_m1)
        self.assertIsNone(sig, f"Naked sweep must be blocked when HTF/SMC criteria not met, got: {diag}")

    @patch("master_live_bot.mt5")
    def test_03_modify_position_sl_uses_trade_stops_level_without_attribute_error(self, mock_mt5):
        from master_live_bot import MasterUnifiedBot

        bot = MasterUnifiedBot(dry_run=True, base_capital=5000.0)
        bot.bridge.resolved_symbols = {"NAS100": "NAS100"}
        bot.bridge.symbol_specs = {"NAS100": {"digits": 2, "point": 0.1}}

        pos = SimpleNamespace(ticket=5739394918, type=0, price_current=27020.0, tp=27145.9)
        mock_mt5.positions_get.return_value = [pos]
        # Real MT5 SymbolInfo has trade_stops_level, NOT stops_level
        mock_mt5.symbol_info.return_value = SimpleNamespace(trade_stops_level=20)
        mock_mt5.symbol_info_tick.return_value = SimpleNamespace(bid=27020.0, ask=27022.0)
        mock_mt5.ORDER_TYPE_SELL = 1
        mock_mt5.TRADE_ACTION_SLTP = 6
        mock_mt5.TRADE_RETCODE_DONE = 10009
        mock_mt5.order_send.return_value = SimpleNamespace(retcode=10009, comment="Done")

        res = bot.modify_position_sl(5739394918, "NAS100", 26790.42)
        self.assertTrue(res)

    @patch("master_live_bot.mt5")
    def test_04_closed_trade_pnl_matches_by_position_id_and_increments_losses(self, mock_mt5):
        from master_live_bot import MasterUnifiedBot

        bot = MasterUnifiedBot(dry_run=False, base_capital=5000.0)
        bot.notifier = MagicMock()
        bot.open_positions = {
            "AUDUSD": {
                "group_id": "AUDUSD_1",
                "strategy": "LEWIS_KELLY_SMC",
                "tickets": [5739441925, 5739441933, 5739441934],
            }
        }
        mock_mt5.positions_get.return_value = []
        mock_mt5.account_info.return_value = SimpleNamespace(balance=6100.0, equity=6100.0)
        # MT5 deal format: opening deals have order==pos_id and profit=0;
        # closing SL deals have DIFFERENT order ticket, entry=1, position_id==ticket!
        deals = [
            SimpleNamespace(order=5739441925, position_id=5739441925, entry=0, ticket=101, profit=0.0, commission=0.0, swap=0.0, comment="LEW_T1"),
            SimpleNamespace(order=5739446360, position_id=5739441925, entry=1, ticket=102, profit=-19.43, commission=0.0, swap=0.0, comment="[sl]"),
            SimpleNamespace(order=5739446361, position_id=5739441933, entry=1, ticket=103, profit=-19.43, commission=0.0, swap=0.0, comment="[sl]"),
            SimpleNamespace(order=5739446362, position_id=5739441934, entry=1, ticket=104, profit=-26.80, commission=0.0, swap=0.0, comment="[sl]"),
        ]
        mock_mt5.history_deals_get.return_value = deals

        bot.manage_active_positions()

        # Must record a loss (consecutive losses == 1), NOT reset to 0!
        self.assertEqual(bot.risk_guardian.symbol_consecutive_losses.get("AUDUSD"), 1)
        call_kwargs = bot.notifier.notify_trade_closed.call_args.kwargs
        self.assertAlmostEqual(call_kwargs["pnl"], -65.66, places=2)

    @patch("master_live_bot.mt5")
    def test_05_stale_bar_rejected_and_circuit_breaker_does_not_crash(self, mock_mt5):
        from master_live_bot import MasterUnifiedBot

        bot = MasterUnifiedBot(dry_run=True, base_capital=5000.0)
        bot.notifier = MagicMock()
        bot.bridge = MagicMock()
        bot.bridge.broker_offset = timedelta(hours=2)
        bot.bridge.resolved_symbols = {}

        # A) Test Circuit Breaker check does NOT raise AttributeError on max_floor_cb_breached
        now_utc = datetime.now(timezone.utc)
        bot.risk_guardian.reset_daily_if_new_day(now_utc, 5000.0, 5000.0)
        mock_mt5.account_info.return_value = SimpleNamespace(balance=5000.0, equity=4750.0, margin_free=4750.0, login=213877054, server="OctaFX-Demo")
        mock_mt5.positions_get.return_value = []
        mock_mt5.history_deals_get.return_value = []
        # Should run cleanly without raising AttributeError
        bot.run_cycle()
        self.assertTrue(bot.risk_guardian.daily_halt_active)

        # B) Test stale bar rejection (> 20m old)
        stale_time = datetime(2026, 11, 26, 10, 15, tzinfo=timezone.utc)
        stale_bar = pd.Series({"open": 1.09, "high": 1.098, "low": 1.096, "close": 1.097, "volume": 100}, name=stale_time)
        current_now = datetime(2026, 11, 27, 5, 20, tzinfo=timezone.utc)
        bot.audit_logger = MagicMock()
        bot.evaluate_lewis_kelly_setup("EURUSD", "EURUSD", stale_bar, current_now)
        # bridge.fetch_multi_timeframe_bundle must NOT even be called for a 19-hour-old bar
        bot.bridge.fetch_multi_timeframe_bundle.assert_not_called()

    @patch("master_live_bot.console")
    @patch("master_live_bot.mt5")
    def test_06_safe_console_print_and_ipc_none_protection(self, mock_mt5, mock_console):
        from master_live_bot import MasterUnifiedBot, safe_console_print

        mock_console.print.side_effect = OSError(233, "No process is on the other end of the pipe")
        # Should NOT raise OSError
        safe_console_print("test message")

        # Execute order + manage_active_positions must survive broken pipe AND positions_get()==None
        bot = MasterUnifiedBot(dry_run=True, base_capital=5000.0)
        bot.notifier = MagicMock()
        bot.bridge = MagicMock()
        bot.bridge.get_specs.return_value = {"digits": 5, "point": 0.0001, "volume_min": 0.01, "volume_step": 0.01}
        bot.bridge.get_latest_quote.return_value = {"spread": 0.5}
        mock_mt5.symbol_info_tick.return_value = SimpleNamespace(ask=1.0950, bid=1.0949)
        bot.execute_order(
            strategy="LEWIS_KELLY_SMC",
            symbol="EURUSD",
            broker_sym="EURUSD",
            signal_type=MagicMock(name="SELL"),
            entry_price=1.0949,
            stop_loss=1.0960,
            tp_levels=[1.0920, 1.0890, 1.0860],
            risk_usd=50.0,
        )
        self.assertIn("EURUSD", bot.open_positions)

        # Transient MT5 IPC failure returning None must NOT drop open_positions
        bot.dry_run = False
        mock_mt5.positions_get.return_value = None
        bot.manage_active_positions()
        self.assertIn("EURUSD", bot.open_positions)

    def test_07_lewis_kelly_engine_emits_confirmed_smc_signal(self):
        """
        Verifies positive signal path: when H4 bearish, M15 bearish + In_Premium + sweep,
        and LTF prints Bearish_FVG + CHoCH, LewisKellySMCEngine emits SELL with 2.5R/5R/8R.
        """
        from core.lewis_kelly_engine import LewisKellySMCEngine

        eng = LewisKellySMCEngine("EURUSD")
        exec_dt = pd.Timestamp("2026-11-25 03:30:00", tz="US/Eastern")
        m15_bar = pd.Series({
            "Trend": "bearish",
            "Active_Session": "London",
            "In_Premium": True,
            "In_Discount": False,
            "Asia_High": 1.0950,
            "Asia_Low": 1.0930,
            "Asia_Ever_Swept_High": True,
            "Asia_Ever_Swept_Low": False,
        })
        h4_bar = pd.Series({"Trend": "bearish"})
        exec_row = pd.Series({
            "Open": 1.0948,
            "High": 1.0952,
            "Low": 1.0942,
            "Close": 1.0944,
            "Volume": 100.0,
            "CHoCH": True,
            "Trend": "bearish",
            "Bearish_FVG": True,
            "Bearish_OB_Low": 1.0949,
        })
        sig, diag = eng.evaluate_bar_state(
            exec_row, exec_dt, m15_bar, h4_bar,
            adr_dict={exec_dt.date(): 0.0080}, default_adr=0.0080
        )
        self.assertIsNotNone(sig)
        self.assertEqual(sig.direction, "SELL")
        self.assertEqual(diag["decision"], "SIGNAL_SELL")
        risk = sig.sl_price - sig.entry_price
        self.assertAlmostEqual(sig.tp1, sig.entry_price - 2.5 * risk, places=6)
        self.assertAlmostEqual(sig.tp2, sig.entry_price - 5.0 * risk, places=6)
        self.assertAlmostEqual(sig.tp3, sig.entry_price - 8.0 * risk, places=6)

    def test_08_opposite_choch_rejected_and_expired_sweep_does_not_rearm(self):
        """
        Verifies:
        1) An opposite-direction LTF CHoCH (Trend='bullish' while armed Short) does not enter SELL.
        2) After the 45-minute window expires, the same swept level does not immediately re-arm.
        """
        from core.lewis_kelly_engine import LewisKellySMCEngine

        eng = LewisKellySMCEngine("EURUSD")
        m15_bar = pd.Series({
            "Trend": "bearish",
            "Active_Session": "London",
            "In_Premium": True,
            "In_Discount": False,
            "Asia_High": 1.0950,
            "Asia_Low": 1.0930,
            "Asia_Ever_Swept_High": True,
            "Asia_Ever_Swept_Low": False,
        })
        h4_bar = pd.Series({"Trend": "bearish"})

        # Bar 1: Opposite (bullish) CHoCH must only arm Short and wait, never trigger SELL
        t0 = pd.Timestamp("2026-11-25 03:00:00", tz="US/Eastern")
        opp_row = pd.Series({
            "Open": 1.0948, "High": 1.0952, "Low": 1.0946, "Close": 1.0951,
            "Volume": 100.0, "CHoCH": True, "Trend": "bullish", "Bearish_FVG": False,
        })
        sig, diag = eng.evaluate_bar_state(
            opp_row, t0, m15_bar, h4_bar, adr_dict={t0.date(): 0.0080}, default_adr=0.0080
        )
        self.assertIsNone(sig)
        self.assertEqual(diag["decision"], "AWAITING_LTF_CHOCH")

        # Bar 2 (+46m): Expires window
        t1 = t0 + pd.Timedelta(minutes=46)
        wait_row = pd.Series({
            "Open": 1.0949, "High": 1.0950, "Low": 1.0947, "Close": 1.0948,
            "Volume": 100.0, "CHoCH": False, "Trend": "none", "Bearish_FVG": False,
        })
        sig2, diag2 = eng.evaluate_bar_state(
            wait_row, t1, m15_bar, h4_bar, adr_dict={t1.date(): 0.0080}, default_adr=0.0080
        )
        self.assertIsNone(sig2)
        self.assertEqual(diag2["decision"], "REJECT_CHOCH_TIMEOUT")

        # Bar 3 (+47m): Same swept session high must stay expired and NOT re-arm
        t2 = t0 + pd.Timedelta(minutes=47)
        sig3, diag3 = eng.evaluate_bar_state(
            wait_row, t2, m15_bar, h4_bar, adr_dict={t2.date(): 0.0080}, default_adr=0.0080
        )
        self.assertIsNone(sig3)
        self.assertIsNone(eng.awaiting_choch)
        self.assertEqual(diag3["decision"], "REJECT_HTF_SMC_FILTER")


if __name__ == "__main__":
    unittest.main()
