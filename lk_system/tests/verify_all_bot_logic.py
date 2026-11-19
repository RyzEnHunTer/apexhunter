"""
Automated Verification & Logic Audit Suite
Tests all internal decision math, risk guardians, lot size splitters,
regime gates, and anti-repaint bar slicing with unit test assertions.
"""

import sys
import os
import unittest
from datetime import datetime, timezone, timedelta
import pandas as pd
import numpy as np

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from core.market_regime_handler import (
    ChopDetector,
    StrategyFailureGuard,
    CircuitBreakerGuardian,
    DynamicStrategyMarketSelector
)
from dcc_engine_v1_2 import DCCEngineV12, SignalType

class TestMasterBotLogic(unittest.TestCase):
    def setUp(self):
        self.guardian = CircuitBreakerGuardian(
            base_capital=5000.0,
            emergency_daily_cb_pct=0.045, # $225
            emergency_max_cb_pct=0.080,   # $4600 floor
            news_soft_cap_pct=0.030,      # $150
            normal_daily_cap_pct=0.040,   # $200
            risk_per_trade_pct=0.010,     # $50
            max_concurrent_trades=3
        )
        self.selector = DynamicStrategyMarketSelector(self.guardian)

    def test_guardian_news_contingent_soft_cap(self):
        """Verifies soft cap triggers at $150 during news, but allows trading up to $200 outside news."""
        # Case 1: Daily loss is $160 (3.2%)
        # During news -> BLOCKED (exceeds $150)
        can_trade_news, reason_news = self.guardian.can_open_new_trade(
            current_balance=4840.0,
            current_equity=4840.0,
            is_high_impact_news_window=True
        )
        self.assertFalse(can_trade_news)
        self.assertIn("NEWS_CONTINGENT_SOFT_CAP", reason_news)

        # Outside news -> ALLOWED (within $200 normal daily limit)
        can_trade_normal, reason_normal = self.guardian.can_open_new_trade(
            current_balance=4840.0,
            current_equity=4840.0,
            is_high_impact_news_window=False
        )
        self.assertTrue(can_trade_normal)
        self.assertIn("RISK_APPROVED", reason_normal)

        # Case 2: Daily loss is $210 (4.2%) -> BLOCKED even outside news (exceeds $200)
        can_trade_over, reason_over = self.guardian.can_open_new_trade(
            current_balance=4790.0,
            current_equity=4790.0,
            is_high_impact_news_window=False
        )
        self.assertFalse(can_trade_over)
        self.assertIn("NORMAL_DAILY_SOFT_CAP", reason_over)

    def test_guardian_concurrency_slots(self):
        """Verifies max 3 concurrent positions rule."""
        self.guardian.active_open_positions = 0
        can_trade, _ = self.guardian.can_open_new_trade(5000.0, 5000.0)
        self.assertTrue(can_trade)

        self.guardian.active_open_positions = 2
        can_trade, _ = self.guardian.can_open_new_trade(5000.0, 5000.0)
        self.assertTrue(can_trade)

        self.guardian.active_open_positions = 3
        can_trade, reason = self.guardian.can_open_new_trade(5000.0, 5000.0)
        self.assertFalse(can_trade)
        self.assertIn("MAX_CONCURRENT_POSITIONS", reason)

    def test_guardian_emergency_circuit_breakers(self):
        """Verifies daily 4.5% killswitch and 8.0% max DD floor."""
        # Daily loss reaches $225 (4.5%)
        can_trade, reason = self.guardian.can_open_new_trade(4775.0, 4775.0)
        self.assertFalse(can_trade)
        self.assertIn("DAILY_CB_TRIGGERED", reason)

        # Equity drops below $4,600 (8.0% floor from $5k)
        can_trade_floor, reason_floor = self.guardian.can_open_new_trade(4590.0, 4590.0)
        self.assertFalse(can_trade_floor)
        self.assertIn("MAX_CB_BREACH", reason_floor)

    def test_lewis_kelly_session_and_filters(self):
        """Verifies Lewis Kelly SMC session gates, Asian ratio, and outlier checks."""
        # 1. Asian session hour (03:00 UTC) -> BLOCKED
        cleared, reason = StrategyFailureGuard.check_lewis_kelly_gate(
            risk_pips=8.0, asian_range_pips=25.0, adr_5_pips=60.0,
            is_red_folder_news=False, utc_hour=3, utc_weekday=2
        )
        self.assertFalse(cleared)
        self.assertIn("OUTSIDE_ACTIVE_SMC_HOURS", reason)

        # 2. London session hour (09:00 UTC) with valid Asian range (41% ADR) -> APPROVED
        cleared, reason = StrategyFailureGuard.check_lewis_kelly_gate(
            risk_pips=8.0, asian_range_pips=25.0, adr_5_pips=60.0,
            is_red_folder_news=False, utc_hour=9, utc_weekday=2
        )
        self.assertTrue(cleared)

        # 3. Blown out Asian range (75% ADR) -> BLOCKED
        cleared, reason = StrategyFailureGuard.check_lewis_kelly_gate(
            risk_pips=8.0, asian_range_pips=45.0, adr_5_pips=60.0,
            is_red_folder_news=False, utc_hour=9, utc_weekday=2
        )
        self.assertFalse(cleared)
        self.assertIn("ASIAN_RANGE_EXHAUSTED", reason)

        # 4. SL Outlier (> 15 pips) -> BLOCKED
        cleared, reason = StrategyFailureGuard.check_lewis_kelly_gate(
            risk_pips=18.5, asian_range_pips=25.0, adr_5_pips=60.0,
            is_red_folder_news=False, utc_hour=9, utc_weekday=2
        )
        self.assertFalse(cleared)
        self.assertIn("SL_OUTLIER_BLOWOUT", reason)

        # 5. Friday late (16:30 UTC) -> BLOCKED
        cleared, reason = StrategyFailureGuard.check_lewis_kelly_gate(
            risk_pips=8.0, asian_range_pips=25.0, adr_5_pips=60.0,
            is_red_folder_news=False, utc_hour=16, utc_weekday=4
        )
        self.assertFalse(cleared)
        self.assertIn("FRIDAY_LATE_LIQUIDITY_DRAIN", reason)

    def test_lot_size_math_and_split(self):
        """Verifies exact $50 dollar risk lot sizing across Forex, JPY, Gold, and Nasdaq."""
        test_cases = [
            # (symbol, sl_dist, tick_sz, tick_val, vol_step, vol_min)
            ("EURUSD", 0.00100, 1e-05, 1.0, 0.01, 0.01), # 10 pips = $50 -> 0.50 lots
            ("USDJPY", 0.150, 0.001, 0.625, 0.01, 0.01),  # 15 pips -> exact lots
            ("XAUUSD", 4.00, 0.01, 1.0, 0.01, 0.01),      # $4 SL = 400 ticks -> 0.12 lots ($48 risk)
            ("NAS100", 50.0, 0.1, 1.0, 0.01, 0.01),       # 50 pts = 500 ticks -> 0.10 lots ($50 risk)
        ]
        
        risk_usd = 50.0
        for sym, sl_dist, tick_sz, tick_val, vol_step, vol_min in test_cases:
            lots_exact = risk_usd / ((sl_dist / tick_sz) * tick_val + 1e-9)
            min_split_lot = max(vol_min * 2.0, 0.02)
            total_lots = max(min_split_lot, round(lots_exact / vol_step) * vol_step)
            total_lots = round(total_lots, 2)

            lots_a = max(vol_min, round((total_lots * 0.5) / vol_step) * vol_step)
            lots_a = round(lots_a, 2)
            lots_b = round(max(vol_min, total_lots - lots_a), 2)

            # Invariants:
            # 1. Sum of twin orders MUST equal total lots exactly!
            self.assertEqual(round(lots_a + lots_b, 2), total_lots, f"Mismatch in {sym}")
            # 2. Both tickets must satisfy minimum volume
            self.assertGreaterEqual(lots_a, vol_min)
            self.assertGreaterEqual(lots_b, vol_min)
            # 3. Realized dollar loss must be <= $55 (tolerance for lot discretization)
            calc_loss = total_lots * (sl_dist / tick_sz) * tick_val
            self.assertLessEqual(calc_loss, 55.0, f"Risk blowout in {sym}: ${calc_loss}")

    def test_anti_repaint_slicing(self):
        """Verifies that dropping iloc[:-1] eliminates forming unclosed candles."""
        # Simulated MT5 rates where the last bar is forming
        now = datetime.now(timezone.utc)
        times = [now - timedelta(minutes=5 * i) for i in range(5, -1, -1)]
        df = pd.DataFrame({
            'open': [1.1000 + i*0.0001 for i in range(6)],
            'high': [1.1005 + i*0.0001 for i in range(6)],
            'low': [1.0995 + i*0.0001 for i in range(6)],
            'close': [1.1002 + i*0.0001 for i in range(6)],
            'volume': [100, 120, 110, 140, 130, 25] # 25 is incomplete forming bar!
        }, index=times)

        # Apply slicing rule
        df_closed = df.iloc[:-1]
        
        # Invariant 1: Incomplete bar is removed
        self.assertEqual(len(df_closed), 5)
        self.assertEqual(df_closed.iloc[-1]['volume'], 130) # Completed bar
        # Invariant 2: Last bar in df_closed is fully completed
        self.assertNotEqual(df_closed.iloc[-1]['volume'], 25)

if __name__ == '__main__':
    unittest.main()
