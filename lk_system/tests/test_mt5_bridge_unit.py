"""
Unit Tests for MT5LiveBridge Watchdog & Fallback Architecture
============================================================
Location: tests/test_mt5_bridge_unit.py
"""

import unittest
from unittest.mock import MagicMock, patch
from datetime import datetime, timezone, timedelta
import pandas as pd
import numpy as np

from core.mt5_live_bridge import MT5LiveBridge

class TestMT5LiveBridgeWatchdog(unittest.TestCase):
    def setUp(self):
        self.bridge = MT5LiveBridge(target_symbols=['EURUSD', 'XAUUSD'])
        self.bridge.resolved_symbols = {'EURUSD': 'EURUSD.pro', 'XAUUSD': 'GOLD'}

    @patch('core.mt5_live_bridge.mt5')
    def test_ensure_connected_already_connected(self, mock_mt5):
        mock_terminal = MagicMock()
        mock_terminal.connected = True
        mock_mt5.terminal_info.return_value = mock_terminal

        self.assertTrue(self.bridge.ensure_connected())
        self.assertTrue(self.bridge.is_connected)
        mock_mt5.initialize.assert_not_called()

    @patch('core.mt5_live_bridge.mt5')
    def test_ensure_connected_reconnect_success(self, mock_mt5):
        # First call terminal_info returns disconnected/None
        mock_mt5.terminal_info.side_effect = [None, MagicMock()]
        mock_mt5.initialize.return_value = True
        mock_mt5.account_info.return_value = MagicMock(login=123456)

        result = self.bridge.ensure_connected()
        self.assertTrue(result)
        self.assertTrue(self.bridge.is_connected)
        mock_mt5.initialize.assert_called_once()
        # Verify symbol_select was called to re-arm symbols in Market Watch
        self.assertGreaterEqual(mock_mt5.symbol_select.call_count, 2)

    @patch('core.mt5_live_bridge.mt5')
    def test_ensure_connected_reconnect_failure(self, mock_mt5):
        mock_mt5.terminal_info.return_value = None
        mock_mt5.initialize.return_value = False

        result = self.bridge.ensure_connected()
        self.assertFalse(result)
        self.assertFalse(self.bridge.is_connected)

    @patch('core.mt5_live_bridge.mt5')
    def test_fetch_rates_fallback_success(self, mock_mt5):
        # First copy_rates_from_pos returns None, then ensure_connected succeeds, second returns data
        mock_terminal = MagicMock()
        mock_terminal.connected = True
        mock_mt5.terminal_info.return_value = mock_terminal

        dummy_rates = np.array([
            (1600000000, 1.1000, 1.1020, 1.0980, 1.1010, 150, 0, 0),
            (1600000300, 1.1010, 1.1030, 1.0990, 1.1025, 200, 0, 0)
        ], dtype=[('time', '<i8'), ('open', '<f8'), ('high', '<f8'), ('low', '<f8'), ('close', '<f8'), ('tick_volume', '<i8'), ('spread', '<i4'), ('real_volume', '<i8')])

        mock_mt5.copy_rates_from_pos.side_effect = [None, dummy_rates]

        with patch.object(self.bridge, 'ensure_connected', return_value=True) as mock_ensure:
            df = self.bridge.fetch_rates('EURUSD', 'M5', 10)
            self.assertIsNotNone(df)
            self.assertEqual(len(df), 2)
            self.assertIn('close', df.columns)
            self.assertIn('volume', df.columns)
            mock_ensure.assert_called_once()

    @patch('core.mt5_live_bridge.mt5')
    def test_get_latest_quote_fallback_success(self, mock_mt5):
        mock_info = MagicMock()
        mock_info.bid = 1.0850
        mock_info.ask = 1.0852
        mock_info.spread = 2
        mock_info.point = 0.00001
        mock_info.digits = 5

        mock_mt5.symbol_info.side_effect = [None, mock_info]

        with patch.object(self.bridge, 'ensure_connected', return_value=True) as mock_ensure:
            quote = self.bridge.get_latest_quote('EURUSD')
            self.assertIsNotNone(quote)
            self.assertEqual(quote['bid'], 1.0850)
            self.assertEqual(quote['ask'], 1.0852)
            mock_ensure.assert_called_once()

    def test_check_new_bar_closed_anti_repaint(self):
        t1 = pd.Timestamp('2026-11-20 12:00:00', tz=timezone.utc)
        t2 = pd.Timestamp('2026-11-20 12:05:00', tz=timezone.utc)
        t3 = pd.Timestamp('2026-11-20 12:10:00', tz=timezone.utc)

        df1 = pd.DataFrame({
            'open': [100.0, 101.0, 102.0],
            'high': [102.0, 103.0, 104.0],
            'low': [99.0, 100.0, 101.0],
            'close': [101.0, 102.0, 103.0],
            'volume': [50, 60, 70]
        }, index=[t1, t2, t3])

        with patch.object(self.bridge, 'fetch_rates', return_value=df1):
            # First call -> initializes last_known to t2, returns False
            is_new, bar = self.bridge.check_new_bar_closed('EURUSD', 'M5')
            self.assertFalse(is_new)
            self.assertEqual(bar.name, t2)

            # Second call with same df -> no new bar closed
            is_new, bar = self.bridge.check_new_bar_closed('EURUSD', 'M5')
            self.assertFalse(is_new)

        # Third call: new bar closed at t3, forming bar at t4
        t4 = pd.Timestamp('2026-11-20 12:15:00', tz=timezone.utc)
        df2 = pd.DataFrame({
            'open': [101.0, 102.0, 103.0],
            'high': [103.0, 104.0, 105.0],
            'low': [100.0, 101.0, 102.0],
            'close': [102.0, 103.0, 104.0],
            'volume': [60, 70, 80]
        }, index=[t2, t3, t4])

        with patch.object(self.bridge, 'fetch_rates', return_value=df2):
            is_new, bar = self.bridge.check_new_bar_closed('EURUSD', 'M5')
            self.assertTrue(is_new)
            self.assertEqual(bar.name, t3)
            self.assertEqual(bar['close'], 103.0)

if __name__ == '__main__':
    unittest.main()
