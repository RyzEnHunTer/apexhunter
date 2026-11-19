import unittest
from datetime import datetime, timezone, timedelta
import os
import sys

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from core.risk_guardian import RiskGuardian
from core.spread_guardian import SpreadGuardian
from core.news_guardian import NewsGuardian
from core.order_manager import OrderManager

class TestMasterBotIntegration(unittest.TestCase):
    """
    Verifies that all 4 guardian modules stitch together seamlessly
    for the master execution pipeline.
    """
    def setUp(self):
        self.risk_guardian = RiskGuardian(
            baseline_capital=5000.0,
            daily_cb_pct=0.040,
            max_floor_cb_pct=0.085,
            recovery_risk_pct=0.002,
            normal_risk_pct=0.010
        )
        self.spread_guardian = SpreadGuardian(max_spread_sl_ratio=0.30)
        self.news_guardian = NewsGuardian(refresh_interval_hours=12, auto_fetch=False)
        self.order_manager = OrderManager(magic_base=88000)

    def test_complete_trade_pipeline_approval(self):
        # 1. 00:00 UTC Daily Anchor
        now_utc = datetime(2026, 3, 10, 14, 0, tzinfo=timezone.utc)
        self.risk_guardian.reset_daily_if_new_day(now_utc, current_balance=5000.0)

        # 2. Check news embargo (No events)
        is_emb, _ = self.news_guardian.is_news_embargo('EURUSD', now_utc)
        self.assertFalse(is_emb)

        # 3. Check spread (EURUSD live spread 1.2 pips on 15 pip SL)
        spread_ok, _ = self.spread_guardian.is_spread_acceptable(
            symbol='EURUSD',
            live_ask=1.08512,
            live_bid=1.08500,
            entry_price=1.08512,
            sl_price=1.08362 # 15 pips SL
        )
        self.assertTrue(spread_ok)

        # 4. Check Risk Guardian sizing
        risk_usd = self.risk_guardian.calculate_fitted_risk_usd(current_balance=5000.0, current_equity=5000.0)
        self.assertEqual(risk_usd, 50.0) # Full 1.0%

        # 5. Calculate lots
        total_lots = self.risk_guardian.calculate_lot_size(
            symbol='EURUSD',
            entry_price=1.08512,
            sl_price=1.08362,
            tick_size=0.00001,
            tick_value=1.0000,
            volume_min=0.01,
            volume_step=0.01,
            risk_usd=risk_usd
        )
        self.assertAlmostEqual(total_lots, 0.33, delta=0.02)

        # 6. Split into 3 orders for Forex SMC
        splits = self.order_manager.calculate_order_splits(
            strategy="LEWIS_KELLY_SMC",
            total_lots=total_lots,
            tp_levels=[1.08887, 1.09262, 1.09712], # 2.5R, 5.0R, 8.0R
            volume_min=0.01,
            volume_step=0.01
        )
        self.assertEqual(len(splits), 3)
        self.assertEqual(splits[0]['label'], 'SMC_TP1_2.5R')
        self.assertEqual(splits[1]['label'], 'SMC_TP2_5.0R')
        self.assertEqual(splits[2]['label'], 'SMC_TP3_8.0R')

    def test_pipeline_blocks_during_news_10m_auto_close(self):
        # Inject upcoming USD CPI at 14:15 UTC
        event_t = datetime(2026, 3, 10, 14, 15, tzinfo=timezone.utc)
        self.news_guardian.high_impact_events = [{
            'title': 'CPI m/m',
            'currency': 'USD',
            'time': event_t
        }]

        # At 14:07 UTC (8m before news)
        now_utc = event_t - timedelta(minutes=8)

        # A) Open position on EURUSD must be auto-closed!
        should_close, desc = self.news_guardian.should_close_before_news('EURUSD', now_utc, close_lead_min=10)
        self.assertTrue(should_close)
        self.assertIn("CPI", desc)

        # B) New entry signal on EURUSD must be embargoed!
        is_emb, _ = self.news_guardian.is_news_embargo('EURUSD', now_utc)
        self.assertTrue(is_emb)

    def test_pipeline_clamps_risk_near_circuit_breaker(self):
        now_utc = datetime(2026, 3, 10, 10, 0, tzinfo=timezone.utc)
        self.risk_guardian.reset_daily_if_new_day(now_utc, current_balance=5000.0)

        # Account is down $180 today (only $20 room before $200 daily limit)
        risk_usd = self.risk_guardian.calculate_fitted_risk_usd(current_balance=4820.0, current_equity=4820.0)
        self.assertAlmostEqual(risk_usd, 20.0, delta=0.5)

        # Total lots should be scaled down proportionally
        lots = self.risk_guardian.calculate_lot_size(
            symbol='EURUSD',
            entry_price=1.08500,
            sl_price=1.08350,
            tick_size=0.00001,
            tick_value=1.0000,
            volume_min=0.01,
            volume_step=0.01,
            risk_usd=risk_usd
        )
        self.assertAlmostEqual(lots, 0.13, delta=0.02) # Exactly $20 / (15 pips * $10) = 0.13 lots!

    def test_self_contained_apexhunter_engine(self):
        from core.apexhunter_engine import DCCEngineV12, SignalType
        import pandas as pd
        import numpy as np

        engine = DCCEngineV12(symbol="XAUUSD")
        self.assertEqual(engine.symbol, "XAUUSD")

        # Create synthetic M5 & H1 data
        idx_5m = pd.date_range("2026-03-10 08:00", "2026-03-10 12:00", freq="5min", tz="UTC")
        df_5m = pd.DataFrame({
            'open': np.linspace(2650, 2660, len(idx_5m)),
            'high': np.linspace(2652, 2662, len(idx_5m)),
            'low': np.linspace(2649, 2659, len(idx_5m)),
            'close': np.linspace(2651, 2661, len(idx_5m)),
            'volume': np.full(len(idx_5m), 100)
        }, index=idx_5m)

        idx_1h = pd.date_range("2026-03-09 00:00", "2026-03-10 12:00", freq="1h", tz="UTC")
        df_1h = pd.DataFrame({
            'open': np.linspace(2640, 2660, len(idx_1h)),
            'high': np.linspace(2645, 2665, len(idx_1h)),
            'low': np.linspace(2638, 2658, len(idx_1h)),
            'close': np.linspace(2642, 2662, len(idx_1h)),
            'volume': np.full(len(idx_1h), 1000)
        }, index=idx_1h)

        prep = engine.prepare_data(df_5m, df_1h)
        self.assertIn('stretch_ratio', prep.columns)
        self.assertIn('chase_ratio', prep.columns)
        self.assertIn('vwap_5m', prep.columns)
        self.assertGreater(len(prep), 0)

    def test_prop_firm_target_lock_and_phase_progression(self):
        from core.account_config_manager import AccountConfigManager
        from types import SimpleNamespace
        import tempfile

        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_cfg = os.path.join(tmp_dir, "bot_accounts_config.json")
            mgr = AccountConfigManager(config_path=tmp_cfg)

            mock_acc = SimpleNamespace(login=998877, balance=5000.0, equity=5000.0, server="Demo", company="Broker")
            cfg = mgr.get_or_setup_account(mock_acc)

            # Phase 1 Target: +8.0% ($5,400)
            self.assertEqual(cfg["current_phase"], 1)
            self.assertEqual(cfg["phase_start_balance"], 5000.0)

            # 1. Check under target
            is_hit, prog = mgr.check_target_progress("998877", current_balance=5300.0)
            self.assertFalse(is_hit)
            self.assertEqual(prog["remaining_dollar"], 100.0)

            # 2. Check hit target
            is_hit, prog = mgr.check_target_progress("998877", current_balance=5400.0)
            self.assertTrue(is_hit)
            self.assertEqual(prog["remaining_dollar"], 0.0)

            # 3. Lock target
            mgr.set_target_locked("998877", True)
            self.assertTrue(mgr.accounts["998877"]["target_locked"])

            # 4. Advance to Phase 2
            mgr.advance_account_phase("998877", new_phase=2, new_start_bal=5400.0)
            cfg2 = mgr.accounts["998877"]
            self.assertEqual(cfg2["current_phase"], 2)
            self.assertEqual(cfg2["phase_start_balance"], 5400.0)
            self.assertFalse(cfg2["target_locked"])

            # Phase 2 Target: +5.0% on $5,400 = $5,670
            is_hit2, prog2 = mgr.check_target_progress("998877", current_balance=5670.0)
            self.assertTrue(is_hit2)

            # 5. Advance to Funded Mode
            mgr.advance_account_phase("998877", new_phase="funded", new_start_bal=5670.0)
            cfg_funded = mgr.accounts["998877"]
            self.assertEqual(cfg_funded["account_lifecycle"], "funded")
            self.assertEqual(cfg_funded["current_phase"], "funded")

if __name__ == '__main__':
    unittest.main()
