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
        for sym in ("EURUSD", "GBPUSD", "AUDUSD", "USDJPY", "EURJPY", "USDCAD"):
            self.assertEqual(PAIRS_CONFIG[sym]["tf_exec"], "M5")
        self.assertAlmostEqual(PAIRS_CONFIG["EURUSD"]["adr_ratio"], 0.60, places=6)
        self.assertAlmostEqual(PAIRS_CONFIG["USDJPY"]["adr_ratio"], 0.675, places=6)

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
        Proves a genuine M5 Asian High wick sweep during London does NOT trigger a trade
        when confirmed H4 trend is bullish (with 3-candle pullback).
        """
        from core.lewis_kelly_engine import LewisKellySMCEngine

        eng = LewisKellySMCEngine("EURUSD")
        # Construct bullish H4 series with 3-candle pullbacks so MarketStructure confirms bullish Trend
        idx_h4 = pd.date_range("2026-11-10 00:00", periods=45, freq="4h", tz="UTC")
        closes_h4 = []
        opens_h4 = []
        highs_h4 = []
        lows_h4 = []
        px = 1.0800
        for i in range(len(idx_h4)):
            if i % 6 in (3, 4, 5):  # 3-candle pullback confirming Swing High/Low cleanly
                o = px
                c = px - 0.0004
                h = o + 0.0002
                l = c - 0.0002
            else:
                o = px
                c = px + 0.0012
                h = c + 0.0003
                l = o - 0.0002
            opens_h4.append(o); closes_h4.append(c); highs_h4.append(h); lows_h4.append(l)
            px = c
        df_h4 = pd.DataFrame({
            "open": opens_h4, "high": highs_h4, "low": lows_h4, "close": closes_h4,
            "volume": np.full(len(idx_h4), 1000),
        }, index=idx_h4)

        # Full Asia + London M5 series (2026-11-24 21:00 UTC .. 2026-11-25 08:30 UTC)
        idx_m5 = pd.date_range("2026-11-24 00:00", "2026-11-25 08:30", freq="5min", tz="UTC")
        df_m5 = pd.DataFrame({
            "open": np.full(len(idx_m5), 1.0900),
            "high": np.full(len(idx_m5), 1.0905),
            "low": np.full(len(idx_m5), 1.0895),
            "close": np.full(len(idx_m5), 1.0900),
            "volume": np.full(len(idx_m5), 100),
        }, index=idx_m5)
        # Inject Asian High sweep wick during London session (08:30 UTC = 03:30 NY)
        df_m5.iloc[-2, df_m5.columns.get_loc("high")] = 1.0912
        df_m5.iloc[-2, df_m5.columns.get_loc("close")] = 1.0898
        df_m5.iloc[-1, df_m5.columns.get_loc("close")] = 1.0897

        df_m15 = df_m5.resample("15min").agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}).dropna()
        # Ensure prior-day ADR_5 history is wider (~30 pips vs 10 pip Asian range) so ADR check passes
        # and blocking is explicitly proven to come from H4 Bullish + M5 HTF filter.
        df_m15.iloc[:40, df_m15.columns.get_loc("high")] = 1.0930
        df_m15.iloc[:40, df_m15.columns.get_loc("low")] = 1.0900

        sig, diag = eng.evaluate(df_h4, df_m15, df_m5)
        self.assertIsNone(sig, f"Naked sweep must be blocked when H4 bullish: {diag}")
        self.assertEqual(diag["h4_trend"], "bullish")
        self.assertGreater(diag["asian_high"], 0.0)
        self.assertEqual(diag["decision"], "REJECT_HTF_SMC_FILTER")

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
        Verifies positive signal path under M5-driven C1: when H4 is bearish and closed M5
        shows bearish trend + In_Premium + sweep + Bearish_FVG + CHoCH, LewisKellySMCEngine
        emits SELL with 2.5R/5R/8R even without a separate M15 bar.
        """
        from core.lewis_kelly_engine import LewisKellySMCEngine

        eng = LewisKellySMCEngine("EURUSD")
        exec_dt = pd.Timestamp("2026-11-25 03:30:00", tz="US/Eastern")
        h4_bar = pd.Series({"Trend": "bearish"})
        exec_row = pd.Series({
            "Open": 1.0948,
            "High": 1.0952,
            "Low": 1.0942,
            "Close": 1.0944,
            "Volume": 100.0,
            "Trend": "bearish",
            "Active_Session": "London",
            "In_Premium": True,
            "In_Discount": False,
            "Asia_High": 1.0950,
            "Asia_Low": 1.0930,
            "Asia_Ever_Swept_High": True,
            "Asia_Ever_Swept_Low": False,
            "CHoCH": True,
            "Bearish_FVG": True,
            "Bearish_OB_Low": 1.0949,
        })
        sig, diag = eng.evaluate_bar_state(
            exec_row, exec_dt, None, h4_bar,
            adr_dict={exec_dt.date(): 0.0080}, default_adr=0.0080
        )
        self.assertIsNotNone(sig)
        self.assertEqual(sig.direction, "SELL")
        self.assertEqual(diag["decision"], "SIGNAL_SELL")
        risk = sig.sl_price - sig.entry_price
        self.assertAlmostEqual(sig.tp1, sig.entry_price - 2.5 * risk, places=6)
        self.assertAlmostEqual(sig.tp2, sig.entry_price - 5.0 * risk, places=6)
        self.assertAlmostEqual(sig.tp3, sig.entry_price - 8.0 * risk, places=6)

    def test_08_opposite_choch_rejected_and_timeout_clears_for_rearm(self):
        """
        Verifies:
        1) An opposite-direction M5 CHoCH (Trend='bullish' while armed Short) does not enter SELL.
        2) After the 45-minute window expires, arming clears cleanly so a subsequent aligned bar
           can re-arm without multi-hour sweep lockout.
        """
        from core.lewis_kelly_engine import LewisKellySMCEngine

        eng = LewisKellySMCEngine("EURUSD")
        h4_bar = pd.Series({"Trend": "bearish"})

        # Bar 1: Opposite (bullish) CHoCH must only arm Short and wait, never trigger SELL
        t0 = pd.Timestamp("2026-11-25 03:00:00", tz="US/Eastern")
        base_m5_context = {
            "Trend": "bearish",
            "Active_Session": "London",
            "In_Premium": True,
            "In_Discount": False,
            "Asia_High": 1.0950,
            "Asia_Low": 1.0930,
            "Asia_Ever_Swept_High": True,
            "Asia_Ever_Swept_Low": False,
        }
        opp_row = pd.Series({
            "Open": 1.0948, "High": 1.0952, "Low": 1.0946, "Close": 1.0951,
            "Volume": 100.0, "CHoCH": True, "Trend": "bullish", "Bearish_FVG": False,
            "Active_Session": "London", "In_Premium": True, "In_Discount": False,
            "Asia_High": 1.0950, "Asia_Low": 1.0930,
            "Asia_Ever_Swept_High": True, "Asia_Ever_Swept_Low": False,
        })
        # Use aligned context bar (Bearish trend + Premium + Sweep) with no SELL CHoCH yet, then opposite
        arm_row = pd.Series({
            "Open": 1.0948, "High": 1.0952, "Low": 1.0946, "Close": 1.0949,
            "Volume": 100.0, "CHoCH": False, "Bearish_FVG": False, **base_m5_context,
        })
        sig0, diag0 = eng.evaluate_bar_state(
            arm_row, t0, None, h4_bar, adr_dict={t0.date(): 0.0080}, default_adr=0.0080
        )
        self.assertIsNone(sig0)
        self.assertEqual(diag0["decision"], "AWAITING_LTF_CHOCH")

        # Bar +5m: Opposite (bullish) CHoCH on M5 must NEVER trigger SELL
        sig, diag = eng.evaluate_bar_state(
            opp_row, t0 + pd.Timedelta(minutes=5), None, h4_bar,
            adr_dict={t0.date(): 0.0080}, default_adr=0.0080,
        )
        self.assertIsNone(sig)
        self.assertEqual(diag["decision"], "AWAITING_LTF_CHOCH")

        # Bar +50m: Expires 45m window
        t1 = t0 + pd.Timedelta(minutes=50)
        wait_row = pd.Series({
            "Open": 1.0949, "High": 1.0950, "Low": 1.0947, "Close": 1.0948,
            "Volume": 100.0, "CHoCH": False, "Bearish_FVG": False, **base_m5_context,
        })
        sig2, diag2 = eng.evaluate_bar_state(
            wait_row, t1, None, h4_bar, adr_dict={t1.date(): 0.0080}, default_adr=0.0080
        )
        self.assertIsNone(sig2)
        self.assertEqual(diag2["decision"], "REJECT_CHOCH_TIMEOUT")
        self.assertIsNone(eng.awaiting_choch)

        # Bar +55m: Still-aligned sweep on M5 re-arms cleanly and confirms on bearish CHoCH
        t2 = t0 + pd.Timedelta(minutes=55)
        rearm_confirm = pd.Series({
            "Open": 1.0949, "High": 1.0951, "Low": 1.0943, "Close": 1.0944,
            "Volume": 100.0, "CHoCH": True, "Bearish_FVG": True, **base_m5_context,
        })
        sig3, diag3 = eng.evaluate_bar_state(
            rearm_confirm, t2, None, h4_bar, adr_dict={t2.date(): 0.0080}, default_adr=0.0080
        )
        self.assertIsNotNone(sig3)
        self.assertEqual(diag3["decision"], "SIGNAL_SELL")

    def test_09_c1_prepare_frames_computes_structure_and_session_on_m5_and_adr_15x(self):
        """
        Verifies prepare_frames() attaches SessionManager/MarketStructure/DealingRange columns
        directly onto the M5 execution frame while preserving ADR_5 calculation from df_m15
        and enforcing the 1.5x ADR threshold (0.60 for EURUSD instead of old 0.40).
        """
        from core.lewis_kelly_engine import LewisKellySMCEngine

        eng = LewisKellySMCEngine("EURUSD")
        idx_h4 = pd.date_range("2026-11-15 00:00", periods=40, freq="4h", tz="UTC")
        df_h4 = pd.DataFrame({
            "open": np.linspace(1.10, 1.08, len(idx_h4)),
            "high": np.linspace(1.102, 1.082, len(idx_h4)),
            "low": np.linspace(1.098, 1.078, len(idx_h4)),
            "close": np.linspace(1.099, 1.079, len(idx_h4)),
            "volume": np.full(len(idx_h4), 1000),
        }, index=idx_h4)

        idx_m15 = pd.date_range("2026-11-18 00:00", "2026-11-25 09:00", freq="15min", tz="UTC")
        df_m15 = pd.DataFrame({
            "open": np.full(len(idx_m15), 1.0900),
            "high": np.full(len(idx_m15), 1.0950),
            "low": np.full(len(idx_m15), 1.0850),
            "close": np.full(len(idx_m15), 1.0900),
            "volume": np.full(len(idx_m15), 200),
        }, index=idx_m15)

        idx_m5 = pd.date_range("2026-11-24 00:00", "2026-11-25 09:00", freq="5min", tz="UTC")
        df_m5 = pd.DataFrame({
            "open": np.full(len(idx_m5), 1.0900),
            "high": np.full(len(idx_m5), 1.0910),
            "low": np.full(len(idx_m5), 1.0890),
            "close": np.full(len(idx_m5), 1.0900),
            "volume": np.full(len(idx_m5), 100),
        }, index=idx_m5)

        _, _, ex_p, adr_d, def_adr = eng.prepare_frames(df_h4, df_m15, df_m5)
        for col in ("Active_Session", "Asia_High", "Asia_Low", "Trend", "In_Premium", "In_Discount", "CHoCH"):
            self.assertIn(col, ex_p.columns)
        self.assertGreater(len(adr_d), 0)

        # Asian range = 0.0050 vs ADR = 0.0100 -> 0.50 ratio: ALLOWED under 1.5x (0.60), blocked under old 0.40
        dt = pd.Timestamp("2026-11-25 03:30:00", tz="US/Eastern")
        row = pd.Series({
            "Open": 1.0948, "High": 1.0952, "Low": 1.0942, "Close": 1.0944,
            "Volume": 100.0, "Trend": "bearish", "Active_Session": "London",
            "In_Premium": True, "In_Discount": False,
            "Asia_High": 1.0950, "Asia_Low": 1.0900,
            "Asia_Ever_Swept_High": True, "Asia_Ever_Swept_Low": False,
            "CHoCH": True, "Bearish_FVG": True,
        })
        sig, diag = eng.evaluate_bar_state(
            row, dt, None, pd.Series({"Trend": "bearish"}),
            adr_dict={dt.date(): 0.0100}, default_adr=0.0100,
        )
        self.assertIsNotNone(sig)
        self.assertEqual(diag["decision"], "SIGNAL_SELL")

    def test_10_causal_structure_nan_ohlc_empty_df_and_jpy_float_guards(self):
        """
        Verifies security & causal invariants raised in code review:
        1) MarketStructure + DealingRange have ZERO prefix lookahead (df[:t] == full[:t]).
        2) NaN OHLC values fail cleanly with REJECT_INVALID_OHLC instead of emitting NaN signals.
        3) Empty df does not crash DealingRangeEngine.calculate_range().
        4) USDJPY clamped min_risk does not false-fail float precision bounds, and post-timeout
           re-arm preserves the session sweep extreme SL anchor.
        """
        from core.market_structure import MarketStructure
        from core.dealing_range import DealingRangeEngine
        from core.lewis_kelly_engine import LewisKellySMCEngine

        # 1) Prefix lookahead check on synthetic multi-swing OHLCV
        idx = pd.date_range("2026-11-24 00:00", periods=30, freq="5min", tz="US/Eastern")
        closes = np.array([
            1.100, 1.102, 1.104, 1.106, 1.108, 1.109, 1.107, 1.105, 1.103, 1.102,
            1.104, 1.106, 1.107, 1.105, 1.103, 1.101, 1.099, 1.100, 1.102, 1.104,
            1.103, 1.101, 1.098, 1.097, 1.099, 1.101, 1.103, 1.102, 1.100, 1.099,
        ])
        df = pd.DataFrame({
            "Open": closes - 0.0005,
            "High": closes + 0.0006,
            "Low": closes - 0.0006,
            "Close": closes,
            "Volume": np.full(30, 100.0),
        }, index=idx)
        full = DealingRangeEngine().calculate_range(MarketStructure(3).update(df))
        check_cols = ["Trend", "Strong_High", "Strong_Low", "Weak_High", "Weak_Low", "In_Premium", "In_Discount"]
        for cut in range(8, 30):
            pref = DealingRangeEngine().calculate_range(MarketStructure(3).update(df.iloc[:cut]))
            pd.testing.assert_frame_equal(
                full[check_cols].iloc[:cut], pref[check_cols], check_dtype=False
            )

        # 2) Empty df guard
        empty_out = DealingRangeEngine().calculate_range(MarketStructure(3).update(df.iloc[:0]))
        self.assertEqual(len(empty_out), 0)
        self.assertIn("In_Premium", empty_out.columns)

        # 3) NaN OHLC rejection + session sweep extreme preservation + USDJPY min_risk float precision
        eng = LewisKellySMCEngine("USDJPY")
        dt = pd.Timestamp("2026-11-25 03:30:00", tz="US/Eastern")
        h4_bar = pd.Series({"Trend": "bearish"})
        nan_row = pd.Series({
            "Open": 152.30, "High": np.nan, "Low": 152.20, "Close": 152.28,
            "Volume": 100.0, "Trend": "bearish", "Active_Session": "London",
            "In_Premium": True, "In_Discount": False, "Asia_High": 152.35, "Asia_Low": 152.00,
            "Asia_Ever_Swept_High": True, "CHoCH": True, "Bearish_FVG": True,
        })
        sig_nan, diag_nan = eng.evaluate_bar_state(nan_row, dt, None, h4_bar, {dt.date(): 0.80}, 0.80)
        self.assertIsNone(sig_nan)
        self.assertEqual(diag_nan["decision"], "REJECT_INVALID_OHLC")

        # Session spike to 152.36 at dt, times out at dt+50m, re-arms + confirms at dt+55m with Close=152.30 ->
        # sweep_extreme must stay 152.36 (not reset to 152.32), giving risk = 152.38 - 152.30 = 0.08 (>= min_risk 0.07)
        spike_row = pd.Series({
            "Open": 152.31, "High": 152.36, "Low": 152.28, "Close": 152.30,
            "Volume": 100.0, "Trend": "bearish", "Active_Session": "London",
            "In_Premium": True, "In_Discount": False, "Asia_High": 152.35, "Asia_Low": 152.00,
            "Asia_Ever_Swept_High": True, "CHoCH": False, "Bearish_FVG": False,
        })
        eng.evaluate_bar_state(spike_row, dt, None, h4_bar, {dt.date(): 0.80}, 0.80)
        wait_row = spike_row.copy()
        wait_row["High"] = 152.32
        eng.evaluate_bar_state(wait_row, dt + pd.Timedelta(minutes=50), None, h4_bar, {dt.date(): 0.80}, 0.80)
        rearm_row = wait_row.copy()
        rearm_row["CHoCH"] = True
        sig_ok, diag_ok = eng.evaluate_bar_state(
            rearm_row, dt + pd.Timedelta(minutes=55), None, h4_bar, {dt.date(): 0.80}, 0.80
        )
        self.assertIsNotNone(sig_ok, f"Expected valid USDJPY re-arm signal, got {diag_ok}")
        self.assertAlmostEqual(sig_ok.sl_price, 152.38, places=4)

        # Clamped min_risk IEEE-754 float boundary regression check:
        # abs((152.30 + 0.07) - 152.30) == 0.06999999999999318 < 0.07 must NOT false-fail min_risk
        eng_clamp = LewisKellySMCEngine("USDJPY")
        clamp_row = pd.Series({
            "Open": 152.31, "High": 152.34, "Low": 152.28, "Close": 152.30,
            "Volume": 100.0, "Trend": "bearish", "Active_Session": "London",
            "In_Premium": True, "In_Discount": False, "Asia_High": 152.35, "Asia_Low": 152.00,
            "Asia_Ever_Swept_High": True, "CHoCH": True, "Bearish_FVG": True,
        })
        sig_clamp, diag_clamp = eng_clamp.evaluate_bar_state(
            clamp_row, dt, None, h4_bar, {dt.date(): 0.80}, 0.80
        )
        self.assertIsNotNone(sig_clamp, f"Clamped USDJPY min_risk must pass float tolerance, got {diag_clamp}")
        self.assertAlmostEqual(sig_clamp.sl_price, 152.37, places=6)


if __name__ == "__main__":
    unittest.main()
