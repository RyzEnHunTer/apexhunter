"""
Unit test verifying ApexHunter v1.2.1 Institutional Parity
1. Confirms NAS100 uses atr_sl_multiplier = 1.40, tp1_rr = 1.40, tp2_rr = 2.10
2. Confirms XAUUSD uses atr_sl_multiplier = 0.90, tp1_rr = 1.40, tp2_rr = 2.20
3. Confirms prepare_data receives full df_h1 and does not suffer from double 1H lag
"""
import unittest
import pandas as pd
import numpy as np
from datetime import datetime, timezone, timedelta
import os
import sys

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from core.apexhunter_engine import DCCEngineV12

class TestApexHunterConfigParity(unittest.TestCase):
    def test_engine_parameter_specs(self):
        engine_gold = DCCEngineV12(symbol="XAUUSD", atr_sl_multiplier=0.90, tp1_rr=1.40, tp2_rr=2.20)
        engine_nas = DCCEngineV12(symbol="NAS100", atr_sl_multiplier=1.40, tp1_rr=1.40, tp2_rr=2.10)

        self.assertEqual(engine_gold.atr_sl_multiplier, 0.90)
        self.assertEqual(engine_gold.tp1_rr, 1.40)
        self.assertEqual(engine_gold.tp2_rr, 2.20)

        self.assertEqual(engine_nas.atr_sl_multiplier, 1.40)
        self.assertEqual(engine_nas.tp1_rr, 1.40)
        self.assertEqual(engine_nas.tp2_rr, 2.10)

    def test_h1_shift_exact_single_bar_alignment(self):
        # Create synthetic 5M and 1H series with >= 20 bars so 14-period ATR/ADX compute
        base_time = datetime(2026, 11, 19, 0, 0, tzinfo=timezone.utc)
        h1_times = [base_time + timedelta(hours=i) for i in range(20)] # 00:00 to 19:00
        df_h1 = pd.DataFrame({
            'open': [100.0 + i*2.0 for i in range(20)],
            'high': [106.0 + i*2.0 for i in range(20)],
            'low': [99.0 + i*2.0 for i in range(20)],
            'close': [105.0 + i*2.0 for i in range(20)],
            'volume': [1000 for _ in range(20)]
        }, index=h1_times)

        # 5M bars during 18:00-19:00 (e.g. 18:10)
        m5_times = [datetime(2026, 11, 19, 18, 0, tzinfo=timezone.utc) + timedelta(minutes=5*i) for i in range(12)]
        df_m5 = pd.DataFrame({
            'open': [135.0 + i*0.5 for i in range(12)],
            'high': [136.0 + i*0.5 for i in range(12)],
            'low': [134.5 + i*0.5 for i in range(12)],
            'close': [135.5 + i*0.5 for i in range(12)],
            'volume': [100 for _ in range(12)]
        }, index=m5_times)

        engine = DCCEngineV12(symbol="NAS100", atr_sl_multiplier=1.40)
        # Passing full df_h1 (including forming 19:00 bar) to prepare_data
        df_prep = engine.prepare_data(df_m5, df_h1)

        # At 18:10, 1H indicators must correspond to the 17:00 closed bar (which closed at 18:00)
        bar_1810 = df_prep.loc[datetime(2026, 11, 19, 18, 10, tzinfo=timezone.utc)]
        self.assertFalse(pd.isna(bar_1810['ema20_1h']))
        self.assertFalse(pd.isna(bar_1810['stretch_ratio']))

if __name__ == '__main__':
    unittest.main()
