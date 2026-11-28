"""
Institutional MetaTrader 5 Live Candle Data Bridge
==================================================
Location: core/mt5_live_bridge.py

Provides a high-speed, zero-error, multi-asset data streaming bridge
connecting MetaTrader 5 to the Artificial Trading Brain (ApexHunter & Lewis Kelly SMC).

Key Capabilities:
1. Auto-Reconnecting MT5 Connection Manager
2. Smart Broker Symbol Auto-Resolver (EURUSD, EURUSD.m, NAS100, USTEC, etc.)
3. Dynamic Broker Timezone Auto-Sync (UTC Normalization)
4. Zero-Lag Multi-Timeframe Rate Ingestion (M1, M5, M15, H1, H4)
5. Anti-Repaint Bar-Close Event Detector (Guarantees entries evaluate on closed candles)
6. Real-time Bid/Ask & Spread Telemetry
"""

import os
import sys
import time
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional, Tuple, Any, Callable
import pandas as pd
import numpy as np
import MetaTrader5 as mt5

from core.asset_handler import AssetHandler

class MT5LiveBridge:
    """
    Industrial-grade MT5 Live Data Bridge for algorithmic trading.
    """

    TIMEFRAMES = {
        'M1': mt5.TIMEFRAME_M1,
        'M5': mt5.TIMEFRAME_M5,
        'M15': mt5.TIMEFRAME_M15,
        'H1': mt5.TIMEFRAME_H1,
        'H4': mt5.TIMEFRAME_H4,
        'D1': mt5.TIMEFRAME_D1,
    }

    def __init__(self, target_symbols: Optional[List[str]] = None, broker_offset_hours: int = 2):
        self.raw_targets = target_symbols or [
            'EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'EURJPY', 'USDCAD',
            'XAUUSD', 'NAS100'
        ]
        self.default_broker_offset_hours = broker_offset_hours
        self.asset_handler = AssetHandler(canonical_targets=self.raw_targets)
        self.resolved_symbols: Dict[str, str] = {} # canonical -> broker symbol
        self.broker_offset: timedelta = timedelta(hours=broker_offset_hours)
        self.is_connected: bool = False
        self.symbol_specs: Dict[str, Dict[str, Any]] = {}
        
        # State tracking for bar-close detection
        self.last_bar_times: Dict[str, Dict[str, datetime]] = {}

    def connect(self) -> bool:
        """Initializes connection to MT5 terminal and auto-detects broker config."""
        if not mt5.initialize():
            print(f"[MT5Bridge ERROR] Failed to initialize MT5: {mt5.last_error()}")
            self.is_connected = False
            return False

        account = mt5.account_info()
        terminal = mt5.terminal_info()
        if account is None or terminal is None:
            print("[MT5Bridge ERROR] Failed to fetch account or terminal info")
            self.is_connected = False
            return False

        self.is_connected = True
        self._resolve_symbols()
        self._sync_broker_timezone()
        self._cache_symbol_specs()
        return True

    def disconnect(self):
        """Cleanly shuts down MT5 connection."""
        if self.is_connected:
            mt5.shutdown()
            self.is_connected = False

    def ensure_connected(self) -> bool:
        """
        Watchdog: Verifies active MT5 IPC connection and auto-reconnects if terminal dropped.
        Prevents live loops from hanging or stalling during network blips or MT5 restarts.
        """
        if mt5 is None:
            return False
        try:
            terminal = mt5.terminal_info()
            if terminal is not None and getattr(terminal, 'connected', True):
                self.is_connected = True
                return True
        except Exception:
            pass

        # Connection dropped or terminal restarted -> Attempt re-initialization
        try:
            if mt5.initialize():
                acc = mt5.account_info()
                term = mt5.terminal_info()
                if acc and term:
                    self.is_connected = True
                    for b_sym in self.resolved_symbols.values():
                        try:
                            mt5.symbol_select(b_sym, True)
                        except Exception:
                            pass
                    self._cache_symbol_specs()
                    return True
        except Exception:
            pass

        self.is_connected = False
        return False

    def _resolve_symbols(self):
        """Resolves canonical symbol names to exact broker symbols using AssetHandler."""
        match_results = self.asset_handler.resolve_all_symbols()
        self.resolved_symbols = {
            canon: res.broker_symbol
            for canon, res in match_results.items()
            if res.status == "ACTIVE" and res.broker_symbol
        }
        for canon, broker_sym in self.resolved_symbols.items():
            try:
                mt5.symbol_select(broker_sym, True)
            except Exception:
                pass
        for canon in self.raw_targets:
            if canon not in self.resolved_symbols:
                print(f"[MT5Bridge WARN] Asset '{canon}' not available on broker -> DISABLED_BY_BROKER")

    def _sync_broker_timezone(self):
        """
        Calculates the broker timezone offset.
        Only syncs dynamically if a recent tick (< 15 min old) is available.
        Otherwise falls back reliably to default_broker_offset_hours.
        """
        sample_sym = self.resolved_symbols.get('XAUUSD', self.resolved_symbols.get('EURUSD'))
        if not sample_sym and self.resolved_symbols:
            sample_sym = list(self.resolved_symbols.values())[0]

        if not sample_sym:
            self.broker_offset = timedelta(hours=self.default_broker_offset_hours)
            return

        tick = mt5.symbol_info_tick(sample_sym)
        now_utc = datetime.now(timezone.utc)
        if tick and abs((now_utc - datetime.fromtimestamp(tick.time, tz=timezone.utc)).total_seconds()) < 900:
            diff_hrs = round((datetime.fromtimestamp(tick.time, tz=timezone.utc) - now_utc).total_seconds() / 3600.0)
            if -12 <= diff_hrs <= 14:
                self.broker_offset = timedelta(hours=diff_hrs)
                return

        self.broker_offset = timedelta(hours=self.default_broker_offset_hours)

    def _cache_symbol_specs(self):
        """Caches contract size, digits, point size, tick value, and spreads."""
        self.symbol_specs = {}
        for canonical, broker_sym in self.resolved_symbols.items():
            info = mt5.symbol_info(broker_sym)
            if info:
                spec_data = {
                    'canonical_symbol': canonical,
                    'broker_symbol': broker_sym,
                    'digits': info.digits,
                    'point': info.point,
                    'spread_pts': info.spread,
                    'contract_size': info.trade_contract_size,
                    'tick_size': info.trade_tick_size,
                    'tick_value': info.trade_tick_value,
                    'bid': info.bid,
                    'ask': info.ask,
                    'volume_min': info.volume_min,
                    'volume_step': info.volume_step,
                    'volume_max': info.volume_max,
                }
                # Store under both canonical ('EURUSD') and broker ('EURUSD.m')
                self.symbol_specs[canonical] = spec_data
                self.symbol_specs[broker_sym] = spec_data
                # Initialize last bar tracking
                self.last_bar_times[canonical] = {}

    def get_specs(self, symbol: str) -> Dict[str, Any]:
        """Safely fetches symbol specifications for canonical or broker symbol."""
        if symbol in self.symbol_specs:
            return self.symbol_specs[symbol]
        for canon, b_sym in self.resolved_symbols.items():
            if canon == symbol or b_sym == symbol:
                return self.symbol_specs.get(canon, {})
        return {}

    def get_latest_quote(self, symbol: str) -> Optional[Dict[str, Any]]:
        """Fetches the latest live Bid, Ask, Spread, and Tick Value."""
        broker_sym = self.resolved_symbols.get(symbol, symbol)
        info = mt5.symbol_info(broker_sym)
        if not info and self.ensure_connected():
            info = mt5.symbol_info(broker_sym)
        if not info:
            return None
        return {
            'symbol': symbol,
            'broker_symbol': broker_sym,
            'bid': info.bid,
            'ask': info.ask,
            'spread': info.spread,
            'spread_pips': info.spread * info.point * (10 if info.digits in [3, 5] else 1),
            'time_utc': datetime.now(timezone.utc)
        }

    def fetch_rates(self, symbol: str, timeframe_str: str = 'M5', count: int = 200) -> Optional[pd.DataFrame]:
        """
        Fetches the latest N rates from MT5 with normalized UTC timestamps.
        Index: DatetimeIndex (UTC)
        Columns: ['open', 'high', 'low', 'close', 'volume']
        """
        broker_sym = self.resolved_symbols.get(symbol, symbol)
        tf_const = self.TIMEFRAMES.get(timeframe_str.upper())
        if tf_const is None:
            raise ValueError(f"Unsupported timeframe: {timeframe_str}")

        rates = mt5.copy_rates_from_pos(broker_sym, tf_const, 0, count)
        if (rates is None or len(rates) == 0) and self.ensure_connected():
            rates = mt5.copy_rates_from_pos(broker_sym, tf_const, 0, count)

        if rates is None or len(rates) == 0:
            return None

        df = pd.DataFrame(rates)
        # Convert timestamp to UTC using broker offset
        df['time'] = pd.to_datetime(df['time'], unit='s', utc=True) - self.broker_offset
        df = df.set_index('time')
        df = df[~df.index.duplicated(keep='last')].sort_index()
        
        # Standardize volume column name
        if 'tick_volume' in df.columns:
            df = df.rename(columns={'tick_volume': 'volume'})
            
        return df[['open', 'high', 'low', 'close', 'volume']]

    def fetch_multi_timeframe_bundle(self, symbol: str) -> Dict[str, pd.DataFrame]:
        """
        Fetches M1, M5, M15, H1, H4 rates in a single unified call for strategy brain consumption.
        """
        bundle = {}
        bundle['M1'] = self.fetch_rates(symbol, 'M1', 400)
        bundle['M5'] = self.fetch_rates(symbol, 'M5', 600)
        bundle['M15'] = self.fetch_rates(symbol, 'M15', 700)
        bundle['H1'] = self.fetch_rates(symbol, 'H1', 150)
        bundle['H4'] = self.fetch_rates(symbol, 'H4', 100)
        return bundle

    def check_new_bar_closed(self, symbol: str, timeframe_str: str = 'M5') -> Tuple[bool, Optional[pd.Series]]:
        """
        Anti-Repaint Gate: Checks if a new bar has just closed.
        Returns: (is_new_bar_closed: bool, closed_bar_series: Optional[pd.Series])
        
        When a new bar forms:
        - Rates[-1] is the new forming bar.
        - Rates[-2] is the just-completed, fully closed bar.
        """
        df = self.fetch_rates(symbol, timeframe_str, count=3)
        if df is None or len(df) < 2:
            return False, None

        closed_bar = df.iloc[-2] # The completed closed bar
        closed_bar_time = closed_bar.name

        last_known = self.last_bar_times.get(symbol, {}).get(timeframe_str)

        if last_known is None:
            # First initialization
            self.last_bar_times.setdefault(symbol, {})[timeframe_str] = closed_bar_time
            return False, closed_bar

        if closed_bar_time > last_known:
            # A new bar has closed!
            self.last_bar_times[symbol][timeframe_str] = closed_bar_time
            return True, closed_bar

        return False, closed_bar

    def compute_session_vwap(self, df_5m: pd.DataFrame) -> pd.Series:
        """Computes true Session VWAP for ApexHunter alignment."""
        tp = (df_5m['high'] + df_5m['low'] + df_5m['close']) / 3.0
        vol = df_5m['volume'].replace(0, 1)
        df_temp = pd.DataFrame({'tp_v': tp * vol, 'vol': vol, 'date': df_5m.index.date}, index=df_5m.index)
        return df_temp.groupby('date')['tp_v'].cumsum() / df_temp.groupby('date')['vol'].cumsum()

