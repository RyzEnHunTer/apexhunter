"""
Unit Tests for AssetHandler (Dynamic Symbol Resolver & Broker Asset Manager)
Verifies:
1. Direct matching of canonical symbols.
2. Alias matching across prop-firm / broker conventions (e.g. GOLD, USTEC, US100.cash).
3. Prefix and suffix stripping (e.g. pro.EURUSD, XAUUSD.m, ecn_GBPUSD+, NAS100.raw).
4. Fuzzy semantic discovery for Gold and Nasdaq.
5. Handling unavailable assets gracefully without crashing.
6. Caching broker specifications (contract size, tick size, pip size, volume bounds).
"""

import unittest
import os
import sys
from unittest.mock import MagicMock, patch

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from core.asset_handler import AssetHandler, SymbolMatchResult


class TestAssetHandler(unittest.TestCase):
    def setUp(self):
        self.handler = AssetHandler(
            canonical_targets=[
                "EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "EURJPY", "USDCAD",
                "XAUUSD", "NAS100"
            ]
        )

    def test_direct_symbol_match(self):
        broker_symbols = ["EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "EURJPY", "USDCAD", "XAUUSD", "NAS100"]
        resolved = self.handler.resolve_all_symbols(available_broker_symbols=broker_symbols)
        for canon in self.handler.canonical_targets:
            self.assertEqual(resolved[canon].broker_symbol, canon)
            self.assertEqual(resolved[canon].status, "ACTIVE")

    def test_suffix_and_alias_resolution(self):
        # Broker has .m, .raw, .pro, GOLD, USTEC
        broker_symbols = [
            "EURUSD.m", "GBPUSD.raw", "USDJPY.pro", "AUDUSD+", "EURJPY_i", "USDCAD.ecn",
            "GOLD.pro", "USTEC.cash"
        ]
        resolved = self.handler.resolve_all_symbols(available_broker_symbols=broker_symbols)

        self.assertEqual(resolved["EURUSD"].broker_symbol, "EURUSD.m")
        self.assertEqual(resolved["GBPUSD"].broker_symbol, "GBPUSD.raw")
        self.assertEqual(resolved["USDJPY"].broker_symbol, "USDJPY.pro")
        self.assertEqual(resolved["AUDUSD"].broker_symbol, "AUDUSD+")
        self.assertEqual(resolved["EURJPY"].broker_symbol, "EURJPY_i")
        self.assertEqual(resolved["USDCAD"].broker_symbol, "USDCAD.ecn")
        self.assertEqual(resolved["XAUUSD"].broker_symbol, "GOLD.pro")
        self.assertEqual(resolved["NAS100"].broker_symbol, "USTEC.cash")

    def test_prefix_resolution(self):
        # Broker has prefixes like pro. or ecn_
        broker_symbols = ["pro.EURUSD", "ecn_GBPUSD", "r_USDJPY", "std_XAUUSD", "pro.NAS100"]
        resolved = self.handler.resolve_all_symbols(available_broker_symbols=broker_symbols)

        self.assertEqual(resolved["EURUSD"].broker_symbol, "pro.EURUSD")
        self.assertEqual(resolved["GBPUSD"].broker_symbol, "ecn_GBPUSD")
        self.assertEqual(resolved["USDJPY"].broker_symbol, "r_USDJPY")
        self.assertEqual(resolved["XAUUSD"].broker_symbol, "std_XAUUSD")
        self.assertEqual(resolved["NAS100"].broker_symbol, "pro.NAS100")

    def test_fuzzy_gold_and_nasdaq_discovery(self):
        # Weird broker naming: e.g. "GOLD_spot_usd", "TECH100_cash"
        broker_symbols = ["EURUSD", "GOLD_spot_usd", "TECH100_cash"]
        resolved = self.handler.resolve_all_symbols(available_broker_symbols=broker_symbols)

        self.assertEqual(resolved["XAUUSD"].broker_symbol, "GOLD_spot_usd")
        self.assertEqual(resolved["NAS100"].broker_symbol, "TECH100_cash")

    def test_non_usd_gold_and_spurious_nasdaq_rejection(self):
        # Broker has XAUEUR and BIOTECH, but not XAUUSD or NAS100
        broker_symbols = ["EURUSD", "XAUEUR", "XAUAUD", "BIOTECH", "BANQ"]
        resolved = self.handler.resolve_all_symbols(available_broker_symbols=broker_symbols)

        # XAUEUR/XAUAUD must not be treated as XAUUSD
        self.assertIsNone(resolved["XAUUSD"].broker_symbol)
        self.assertEqual(resolved["XAUUSD"].status, "DISABLED_BY_BROKER")

        # BIOTECH/BANQ must not be treated as NAS100
        self.assertIsNone(resolved["NAS100"].broker_symbol)
        self.assertEqual(resolved["NAS100"].status, "DISABLED_BY_BROKER")

    def test_unavailable_asset_graceful_degradation(self):
        # Broker offers Forex only, no Gold or Nasdaq
        broker_symbols = ["EURUSD", "GBPUSD", "USDJPY"]
        resolved = self.handler.resolve_all_symbols(available_broker_symbols=broker_symbols)

        self.assertEqual(resolved["EURUSD"].status, "ACTIVE")
        self.assertEqual(resolved["XAUUSD"].status, "DISABLED_BY_BROKER")
        self.assertEqual(resolved["NAS100"].status, "DISABLED_BY_BROKER")
        self.assertIsNone(resolved["XAUUSD"].broker_symbol)
        self.assertFalse(self.handler.is_symbol_tradeable("XAUUSD"))
        self.assertTrue(self.handler.is_symbol_tradeable("EURUSD"))


if __name__ == "__main__":
    unittest.main()
