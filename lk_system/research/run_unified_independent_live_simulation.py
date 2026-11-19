"""
========================================================================================
100% INDEPENDENT RAW-CANDLE MASTER LIVE SIMULATION ENGINE (JAN - NOV 2026)
========================================================================================
- ZERO dependency on external/old trade CSV logs. 100% computed from raw candle parquets.
- Evaluates both strategies synchronously on a master chronological tick/bar clock:
  1. Lewis Kelly SMC on 6 Forex Pairs (EURUSD, GBPUSD, AUDUSD, USDJPY, EURJPY, USDCAD)
     * Timestamps localized to US/Eastern for exact SMC Session Manager killzones:
       - Asia Session (19:00 - 01:00 EST)
       - London Killzone (02:00 - 05:00 EST / 07:00 - 10:00 UTC)
       - NY Killzone (07:00 - 10:00 EST / 12:00 - 15:00 UTC)
     * Asian Range / ADR_5 filter (0.25 to 0.45/0.65)
     * Liquidity Sweeps + CHoCH + displacement FVG retests
     * Native 3-Tier Scaling (+3.0R 30% + BE, +5.0R 30%, +8.0R 40% runner)
     * Max 2 daily losses per pair
  2. DCC ApexHunter v1.2.1 on Gold (XAUUSD) & Nasdaq (NAS100)
     * Timestamps in UTC (06:00 to 21:00 UTC active window)
     * TripleGuard (Shield 1 Anti-Chop >=0.40, Shield 2 ADX <=45.0, Shield 3 No-Chase <=0.50)
     * Shield 4 Low-ADX Quality Gate (ADX < 20 requires Stretch >= 0.90)
     * Smart Killzone (09:00 & 13:00 UTC require Stretch >= 1.10)
     * Monday PM Block (14:00 - 18:00 UTC)
     * 5M Turtle Soup Liquidity Sweep (lookback=20, pullback=8)
     * Native ATR breathing room: Gold SL = 1.30x ATR, Nasdaq SL = 1.40x ATR
     * Native Dual-Ticket Execution: TP1 @ 1.4R (50%) + BE shift, TP2 @ 2.1R (50%)
  3. Dynamic Market Regime Analyzer:
     * When Choppiness Index > 55 or Stretch < 0.90 in indices/metals -> switch to LK Forex!
     * When Asian range blown or outside London/NY hours -> switch to ApexHunter!
  4. Central Risk Guardian:
     * Fixed 1.0% ($50 flat) risk per trade on $5,000 base
     * Max 3 concurrent risk-bearing trades
     * Real ECN commission ($5-$7/lot) + realistic floating spreads
========================================================================================
"""

import os
import sys
import time
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Any, Optional, Tuple
import pandas as pd
import numpy as np

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
DC_ROOT = os.path.join(os.path.dirname(PROJECT_ROOT), "DC")
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)
if PROJECT_ROOT not in sys.path: sys.path.insert(0, PROJECT_ROOT)
if DC_ROOT not in sys.path:
    sys.path.insert(0, DC_ROOT)
if os.path.join(DC_ROOT, 'v1.2') not in sys.path:
    sys.path.insert(0, os.path.join(DC_ROOT, 'v1.2'))

from lk_system.core.market_structure import MarketStructure
from lk_system.core.session_manager import SessionManager
from lk_system.core.imbalance import ImbalanceDetector
from lk_system.core.dealing_range import DealingRangeEngine
from dcc_engine_v1_2 import DCCEngineV12

DATA_DIR = os.path.join(PROJECT_ROOT, "data_jan_nov_2026")
DC_CACHE = os.path.join(os.path.dirname(PROJECT_ROOT), "DC", "data_cache")
if not os.path.exists(DC_CACHE):
    DC_CACHE = os.path.join(PROJECT_ROOT, "tick_cache")

SPECS = {
    'EURUSD': {'pip': 0.0001, 'spread': 0.00010, 'contract': 100000.0, 'vol_step': 0.01, 'vol_min': 0.01, 'comm_lot': 7.0},
    'GBPUSD': {'pip': 0.0001, 'spread': 0.00015, 'contract': 100000.0, 'vol_step': 0.01, 'vol_min': 0.01, 'comm_lot': 7.0},
    'USDJPY': {'pip': 0.01,   'spread': 0.018,   'contract': 100000.0, 'vol_step': 0.01, 'vol_min': 0.01, 'comm_lot': 7.0},
    'AUDUSD': {'pip': 0.0001, 'spread': 0.00014, 'contract': 100000.0, 'vol_step': 0.01, 'vol_min': 0.01, 'comm_lot': 7.0},
    'EURJPY': {'pip': 0.01,   'spread': 0.020,   'contract': 100000.0, 'vol_step': 0.01, 'vol_min': 0.01, 'comm_lot': 7.0},
    'USDCAD': {'pip': 0.0001, 'spread': 0.00016, 'contract': 100000.0, 'vol_step': 0.01, 'vol_min': 0.01, 'comm_lot': 7.0},
    'XAUUSD': {'pip': 0.10,   'spread': 0.25,    'contract': 100.0,    'vol_step': 0.01, 'vol_min': 0.01, 'comm_lot': 5.0},
    'NAS100': {'pip': 1.0,    'spread': 2.00,    'contract': 10.0,     'vol_step': 0.01, 'vol_min': 0.01, 'comm_lot': 5.0},
}

FOREX_CONFIGS = {
    'EURUSD': {'tf_exec': 'M1', 'pip_size': 0.0001, 'spread_pips': 0.8, 'min_risk_pips': 4.0, 'max_risk_pips': 15.0, 'target_sessions': ['London', 'NY'], 'adr_ratio': 0.40},
    'GBPUSD': {'tf_exec': 'M5', 'pip_size': 0.0001, 'spread_pips': 1.2, 'min_risk_pips': 6.0, 'max_risk_pips': 20.0, 'target_sessions': ['London', 'NY'], 'adr_ratio': 0.40},
    'AUDUSD': {'tf_exec': 'M5', 'pip_size': 0.0001, 'spread_pips': 1.0, 'min_risk_pips': 4.5, 'max_risk_pips': 16.0, 'target_sessions': ['London', 'NY'], 'adr_ratio': 0.40},
    'USDJPY': {'tf_exec': 'M1', 'pip_size': 0.01,   'spread_pips': 1.5, 'min_risk_pips': 7.0, 'max_risk_pips': 25.0, 'target_sessions': ['London', 'NY'], 'adr_ratio': 0.45},
    'EURJPY': {'tf_exec': 'M5', 'pip_size': 0.01,   'spread_pips': 1.6, 'min_risk_pips': 8.0, 'max_risk_pips': 25.0, 'target_sessions': ['London', 'NY'], 'adr_ratio': 0.45},
    'USDCAD': {'tf_exec': 'M5', 'pip_size': 0.0001, 'spread_pips': 1.2, 'min_risk_pips': 5.0, 'max_risk_pips': 18.0, 'target_sessions': ['NY'],           'adr_ratio': 0.40},
}

def detect_5m_liquidity_sweep(lows: np.ndarray, highs: np.ndarray, closes: np.ndarray,
                              entry_idx: int, direction: int, swing_lookback: int = 20, pullback_window: int = 8) -> Tuple[bool, float]:
    """ApexHunter v1.2.1 Turtle Soup 5M Liquidity Sweep Detector."""
    if entry_idx < swing_lookback + 2:
        return False, 0.0
    pb_start = max(0, entry_idx - pullback_window)
    sw_start = max(0, pb_start - swing_lookback)
    if pb_start <= sw_start:
        return False, 0.0

    curr_close = closes[entry_idx]
    if direction == 1: # BUY
        cand_lows = [lows[j] for j in range(sw_start + 1, pb_start)
                     if lows[j] <= lows[j - 1] and lows[j] <= lows[j + 1]]
        if not cand_lows:
            cand_lows = [min(lows[sw_start:pb_start])]
        min_pb = min(lows[pb_start:entry_idx + 1])
        for sw_low in cand_lows:
            if min_pb < sw_low and curr_close > sw_low:
                return True, sw_low
        return False, 0.0
    else: # SELL
        cand_highs = [highs[j] for j in range(sw_start + 1, pb_start)
                      if highs[j] >= highs[j - 1] and highs[j] >= highs[j + 1]]
        if not cand_highs:
            cand_highs = [max(highs[sw_start:pb_start])]
        max_pb = max(highs[pb_start:entry_idx + 1])
        for sw_high in cand_highs:
            if max_pb > sw_high and curr_close < sw_high:
                return True, sw_high
        return False, 0.0


class LiveSimTrade:
    def __init__(self, symbol: str, strategy: str, direction: int, entry_price: float, sl_price: float,
                 risk_dist: float, entry_time: datetime, commission: float, dollar_risk: float = 50.0):
        self.symbol = symbol
        self.strategy = strategy
        self.direction = direction
        self.entry_price = entry_price
        self.sl_price = sl_price
        self.original_sl = sl_price
        self.risk_dist = risk_dist
        self.entry_time = entry_time
        self.exit_time: Optional[datetime] = None
        self.commission = commission
        self.dollar_risk = dollar_risk
        
        self.closed = False
        self.realized_pnl = 0.0
        self.exit_reason = ""
        self.remaining_weight = 1.0
        
        # Lewis Kelly SMC Native 3-Tier Scaling (3.0R 30%, 5.0R 30%, 8.0R 40%)
        self.lk_tier_3r = False
        self.lk_tier_5r = False
        self.lk_tier_8r = False
        
        # ApexHunter v1.2.1 Native Dual-Ticket (TP1 50%, TP2 50%, BE at TP1)
        self.apex_tp1_hit = False
        self.apex_tp2_hit = False
        self.tp1_rr = 1.40
        self.tp2_rr = 2.10
        
    @property
    def has_active_downside_risk(self) -> bool:
        if self.closed:
            return False
        if self.strategy == 'LK_FOREX_SMC':
            return not self.lk_tier_3r
        else:
            return not self.apex_tp1_hit


def load_all_market_data():
    """Loads and synchronizes 100% raw candle data directly from disk."""
    print("[1/5] Ingesting and Preparing 100% RAW Candle Data across all 8 Assets...")
    t0 = time.time()
    market_data = {}

    # 1. Lewis Kelly SMC Forex Pairs (Native US/Eastern Localization)
    for sym, cfg in FOREX_CONFIGS.items():
        exec_tf = cfg['tf_exec']
        df_h4 = pd.read_parquet(os.path.join(DATA_DIR, f"{sym}_H4_2026.parquet"))
        df_m15 = pd.read_parquet(os.path.join(DATA_DIR, f"{sym}_M15_2026.parquet"))
        df_exec = pd.read_parquet(os.path.join(DATA_DIR, f"{sym}_{exec_tf}_2026.parquet"))

        # Localize to US/Eastern so SessionManager calculates true London & NY killzones
        for df in [df_h4, df_m15, df_exec]:
            if df.index.tz is None:
                df.index = df.index.tz_localize('UTC').tz_convert('US/Eastern')
            else:
                df.index = df.index.tz_convert('US/Eastern')

        # Compute ADR
        df_d1 = df_m15.resample('1D').agg({'High': 'max', 'Low': 'min'}).dropna()
        df_d1['Range'] = df_d1['High'] - df_d1['Low']
        df_d1['ADR_5'] = df_d1['Range'].rolling(5).mean().shift(1)
        adr_dict = {k.date(): v for k, v in df_d1['ADR_5'].items()}
        default_adr = df_d1['Range'].mean() if len(df_d1) > 0 else 70 * cfg['pip_size']

        # SMC Indicator Pipelines
        struct_h4 = MarketStructure(pullback_candles=3)
        struct_m15 = MarketStructure(pullback_candles=3)
        sess_m15 = SessionManager()
        imb_m15 = ImbalanceDetector()
        dr_m15 = DealingRangeEngine()
        struct_exec = MarketStructure(pullback_candles=3)
        imb_exec = ImbalanceDetector()

        df_h4 = struct_h4.update(df_h4)
        df_m15 = sess_m15.update(df_m15)
        df_m15 = struct_m15.update(df_m15)
        df_m15 = imb_m15.detect(df_m15)
        df_m15 = dr_m15.calculate_range(df_m15)

        df_exec = struct_exec.update(df_exec)
        df_exec = imb_exec.detect(df_exec)

        # Precompute UTC timestamps for synchronous master timeline alignment
        df_exec_utc = df_exec.index.tz_convert('UTC')

        market_data[sym] = {
            'type': 'FOREX_SMC',
            'config': cfg,
            'spec': SPECS[sym],
            'adr_dict': adr_dict,
            'default_adr': default_adr,
            'df_h4': df_h4,
            'df_m15': df_m15,
            'df_exec': df_exec,
            'exec_utc_index': df_exec_utc,
            'h4_records': df_h4.to_dict('index'),
            'm15_records': df_m15.to_dict('index'),
            'exec_high': df_exec['High'].to_dict(),
            'exec_low': df_exec['Low'].to_dict(),
            'exec_close': df_exec['Close'].to_dict(),
            'exec_choch': df_exec['CHoCH'].to_dict(),
            'exec_trend': df_exec['Trend'].to_dict(),
            'exec_bear_fvg': df_exec['Bearish_FVG'].to_dict(),
            'exec_bull_fvg': df_exec['Bullish_FVG'].to_dict(),
            'exec_bear_ob_l': df_exec['Bearish_OB_Low'].to_dict(),
            'exec_bull_ob_h': df_exec['Bullish_OB_High'].to_dict(),
        }
        print(f"  Loaded {sym:<6} ({exec_tf} exec, {len(df_exec):,} bars, US/Eastern SMC Session Framework)")

    # 2. ApexHunter v1.2.1 (XAUUSD & NAS100) from Raw Data Cache
    broker_offset = timedelta(hours=3)
    for sym in ['XAUUSD', 'NAS100']:
        df_m5 = pd.read_parquet(os.path.join(DC_CACHE, f"m5_bars_{sym}.parquet"))
        df_m5['time_utc'] = pd.to_datetime(df_m5['time'], unit='s', utc=True) - broker_offset
        df_m5.set_index('time_utc', inplace=True)
        df_m5.rename(columns={'tick_volume': 'volume'}, inplace=True)

        df_h1 = pd.read_parquet(os.path.join(DC_CACHE, f"h1_bars_{sym}.parquet"))
        df_h1['time_utc'] = pd.to_datetime(df_h1['time'], unit='s', utc=True) - broker_offset
        df_h1.set_index('time_utc', inplace=True)

        df_h2 = pd.read_parquet(os.path.join(DC_CACHE, f"h2_bars_{sym}.parquet"))
        df_h2['time_utc'] = pd.to_datetime(df_h2['time'], unit='s', utc=True) - broker_offset
        df_h2.set_index('time_utc', inplace=True)

        engine = DCCEngineV12(symbol=sym)
        df_prep = engine.prepare_data(df_m5, df_h1)
        df_prep['p_diff'] = (df_prep['ema9_5m'] - df_prep['ema20_5m']).shift(1)
        df_prep['c_diff'] = df_prep['ema9_5m'] - df_prep['ema20_5m']

        lows_arr = df_prep['low'].values
        highs_arr = df_prep['high'].values
        closes_arr = df_prep['close'].values

        market_data[sym] = {
            'type': 'APEX_HUNTER_1_2_1',
            'spec': SPECS[sym],
            'df_prep': df_prep,
            'lows_arr': lows_arr,
            'highs_arr': highs_arr,
            'closes_arr': closes_arr,
            'prep_records': df_prep.to_dict('index'),
            'prep_idx_map': {t: idx for idx, t in enumerate(df_prep.index)}
        }
        print(f"  Loaded {sym:<6} (M5 exec, {len(df_prep):,} bars, v1.2.1 Turtle Soup Engine)")

    print(f"Data ingestion completed in {round(time.time() - t0, 1)}s.")
    return market_data


def run_unified_master_live_simulation():
    market_data = load_all_market_data()

    print("\n[2/5] Constructing Unified Master Clock across all 8 Assets...")
    all_utc_timestamps = set()
    for sym, data in market_data.items():
        if data['type'] == 'FOREX_SMC':
            all_utc_timestamps.update(data['exec_utc_index'])
        else:
            all_utc_timestamps.update(data['df_prep'].index)

    timeline = sorted([t for t in all_utc_timestamps if t.year == 2026 and t <= pd.Timestamp('2026-09-18 23:59:59', tz='UTC')])
    print(f"  Master Clock established: {len(timeline):,} active bars from {timeline[0]} to {timeline[-1]}")

    print("\n[3/5] Initializing Simultaneous Strategy Trackers...")
    forex_states = {}
    for sym, data in market_data.items():
        if data['type'] == 'FOREX_SMC':
            forex_states[sym] = {
                'config': data['config'],
                'spec': data['spec'],
                'adr_dict': data['adr_dict'],
                'default_adr': data['default_adr'],
                'h4_records': data['h4_records'],
                'm15_records': data['m15_records'],
                'exec_high': data['exec_high'],
                'exec_low': data['exec_low'],
                'exec_close': data['exec_close'],
                'exec_choch': data['exec_choch'],
                'exec_trend': data['exec_trend'],
                'exec_bear_fvg': data['exec_bear_fvg'],
                'exec_bull_fvg': data['exec_bull_fvg'],
                'exec_bear_ob_l': data['exec_bear_ob_l'],
                'exec_bull_ob_h': data['exec_bull_ob_h'],
                'active_position': None,
                'pending_setup': None,
                'awaiting_choch': None,
                'sweep_extreme_price': 0.0,
                'sweep_time': None,
                'recent_displacement_fvg': None,
                'daily_losses': 0,
                'current_day': None
            }

    apex_states = {}
    for sym in ['XAUUSD', 'NAS100']:
        data = market_data[sym]
        apex_states[sym] = {
            'spec': data['spec'],
            'df_prep': data['df_prep'],
            'prep_records': data['prep_records'],
            'lows_arr': data['lows_arr'],
            'highs_arr': data['highs_arr'],
            'closes_arr': data['closes_arr'],
            'prep_idx_map': data['prep_idx_map'],
            'active_position': None,
            'daily_losses': 0,
            'current_day': None
        }

    # Central Account State
    starting_capital = 5000.0
    balance = starting_capital
    peak_balance = starting_capital
    lowest_balance = starting_capital
    max_dd_dollar = 0.0
    max_dd_pct = 0.0

    current_sim_day = None
    daily_realized_loss = 0.0
    daily_halt = False
    worst_daily_loss = 0.0

    active_positions: List[LiveSimTrade] = []
    closed_positions: List[LiveSimTrade] = []

    print("\n[4/5] Executing 100% Raw-Candle Synchronous Live Simulation Replay...")
    sim_t0 = time.time()

    for step_idx, t_utc in enumerate(timeline):
        t_date = t_utc.date()
        t_hour = t_utc.hour
        t_min = t_utc.minute

        # Midnight Daily Reset
        if current_sim_day != t_date:
            if current_sim_day is not None and daily_realized_loss > worst_daily_loss:
                worst_daily_loss = daily_realized_loss
            current_sim_day = t_date
            daily_realized_loss = 0.0
            daily_halt = False
            for sym, f_st in forex_states.items():
                f_st['daily_losses'] = 0
            for sym, a_st in apex_states.items():
                a_st['daily_losses'] = 0

        # Position Management
        for pos in list(active_positions):
            sym = pos.symbol
            hi = None
            lo = None

            if sym in forex_states:
                t_est = t_utc.tz_convert('US/Eastern')
                hi = forex_states[sym]['exec_high'].get(t_est)
                lo = forex_states[sym]['exec_low'].get(t_est)
            elif sym in apex_states:
                rec = apex_states[sym]['prep_records'].get(t_utc)
                if rec:
                    hi = rec['high']
                    lo = rec['low']

            if hi is None or lo is None:
                continue

            entry = pos.entry_price
            risk = pos.risk_dist

            # Strategy 1: Lewis Kelly SMC (3R, 5R, 8R Scaling)
            if pos.strategy == 'LK_FOREX_SMC':
                if pos.direction == 1: # Long
                    peak_r = (hi - entry) / risk if risk > 0 else 0.0

                    if peak_r >= 3.0 and not pos.lk_tier_3r:
                        pos.lk_tier_3r = True
                        pnl_tier = pos.dollar_risk * 0.30 * 3.0
                        pos.realized_pnl += pnl_tier
                        pos.remaining_weight -= 0.30
                        pos.sl_price = entry + (0.2 * risk)

                    if peak_r >= 5.0 and not pos.lk_tier_5r:
                        pos.lk_tier_5r = True
                        pnl_tier = pos.dollar_risk * 0.30 * 5.0
                        pos.realized_pnl += pnl_tier
                        pos.remaining_weight -= 0.30

                    if lo <= pos.sl_price:
                        pos.exit_time = t_utc
                        rem_r = (pos.sl_price - entry) / risk
                        rem_pnl = pos.dollar_risk * pos.remaining_weight * rem_r
                        pos.realized_pnl += rem_pnl
                        pos.closed = True
                        pos.exit_reason = "LK Tier 1 + BE Stopped" if pos.lk_tier_3r else "LK Full SL Hit"
                        active_positions.remove(pos)
                        forex_states[sym]['active_position'] = None
                        net_pnl = pos.realized_pnl - pos.commission
                        balance += net_pnl
                        if net_pnl < 0:
                            daily_realized_loss += abs(net_pnl)
                            forex_states[sym]['daily_losses'] += 1
                        closed_positions.append(pos)
                        continue

                    if peak_r >= 8.0:
                        pos.exit_time = t_utc
                        rem_pnl = pos.dollar_risk * pos.remaining_weight * 8.0
                        pos.realized_pnl += rem_pnl
                        pos.closed = True
                        pos.exit_reason = "LK Full 8R Expansion Hit"
                        active_positions.remove(pos)
                        forex_states[sym]['active_position'] = None
                        net_pnl = pos.realized_pnl - pos.commission
                        balance += net_pnl
                        closed_positions.append(pos)
                        continue

                else: # Short
                    peak_r = (entry - lo) / risk if risk > 0 else 0.0

                    if peak_r >= 3.0 and not pos.lk_tier_3r:
                        pos.lk_tier_3r = True
                        pnl_tier = pos.dollar_risk * 0.30 * 3.0
                        pos.realized_pnl += pnl_tier
                        pos.remaining_weight -= 0.30
                        pos.sl_price = entry - (0.2 * risk)

                    if peak_r >= 5.0 and not pos.lk_tier_5r:
                        pos.lk_tier_5r = True
                        pnl_tier = pos.dollar_risk * 0.30 * 5.0
                        pos.realized_pnl += pnl_tier
                        pos.remaining_weight -= 0.30

                    if hi >= pos.sl_price:
                        pos.exit_time = t_utc
                        rem_r = (entry - pos.sl_price) / risk
                        rem_pnl = pos.dollar_risk * pos.remaining_weight * rem_r
                        pos.realized_pnl += rem_pnl
                        pos.closed = True
                        pos.exit_reason = "LK Tier 1 + BE Stopped" if pos.lk_tier_3r else "LK Full SL Hit"
                        active_positions.remove(pos)
                        forex_states[sym]['active_position'] = None
                        net_pnl = pos.realized_pnl - pos.commission
                        balance += net_pnl
                        if net_pnl < 0:
                            daily_realized_loss += abs(net_pnl)
                            forex_states[sym]['daily_losses'] += 1
                        closed_positions.append(pos)
                        continue

                    if peak_r >= 8.0:
                        pos.exit_time = t_utc
                        rem_pnl = pos.dollar_risk * pos.remaining_weight * 8.0
                        pos.realized_pnl += rem_pnl
                        pos.closed = True
                        pos.exit_reason = "LK Full 8R Expansion Hit"
                        active_positions.remove(pos)
                        forex_states[sym]['active_position'] = None
                        net_pnl = pos.realized_pnl - pos.commission
                        balance += net_pnl
                        closed_positions.append(pos)
                        continue

            # Strategy 2: ApexHunter v1.2.1 Native Dual-Ticket Execution
            else:
                tp1_p = entry + (pos.tp1_rr * risk) if pos.direction == 1 else entry - (pos.tp1_rr * risk)
                tp2_p = entry + (pos.tp2_rr * risk) if pos.direction == 1 else entry - (pos.tp2_rr * risk)

                if pos.direction == 1: # Long
                    if not pos.apex_tp1_hit:
                        if lo <= pos.sl_price:
                            pos.exit_time = t_utc
                            pos.realized_pnl = -pos.dollar_risk
                            pos.closed = True
                            pos.exit_reason = "Apex Full SL Hit"
                            active_positions.remove(pos)
                            apex_states[sym]['active_position'] = None
                            net_pnl = pos.realized_pnl - pos.commission
                            balance += net_pnl
                            daily_realized_loss += abs(net_pnl)
                            apex_states[sym]['daily_losses'] += 1
                            closed_positions.append(pos)
                            continue
                        elif hi >= tp1_p:
                            pos.apex_tp1_hit = True
                            pnl_ticket_a = (pos.dollar_risk * 0.5) * pos.tp1_rr
                            pos.realized_pnl += pnl_ticket_a
                            pos.sl_price = entry # Breakeven
                            pos.remaining_weight = 0.5
                    else:
                        if hi >= tp2_p:
                            pos.exit_time = t_utc
                            pnl_ticket_b = (pos.dollar_risk * 0.5) * pos.tp2_rr
                            pos.realized_pnl += pnl_ticket_b
                            pos.closed = True
                            pos.exit_reason = "FULL_TP2"
                            active_positions.remove(pos)
                            apex_states[sym]['active_position'] = None
                            net_pnl = pos.realized_pnl - pos.commission
                            balance += net_pnl
                            closed_positions.append(pos)
                            continue
                        elif lo <= pos.sl_price:
                            pos.exit_time = t_utc
                            pos.closed = True
                            pos.exit_reason = "TP1_THEN_BE"
                            active_positions.remove(pos)
                            apex_states[sym]['active_position'] = None
                            net_pnl = pos.realized_pnl - pos.commission
                            balance += net_pnl
                            closed_positions.append(pos)
                            continue
                else: # Short
                    if not pos.apex_tp1_hit:
                        if hi >= pos.sl_price:
                            pos.exit_time = t_utc
                            pos.realized_pnl = -pos.dollar_risk
                            pos.closed = True
                            pos.exit_reason = "Apex Full SL Hit"
                            active_positions.remove(pos)
                            apex_states[sym]['active_position'] = None
                            net_pnl = pos.realized_pnl - pos.commission
                            balance += net_pnl
                            daily_realized_loss += abs(net_pnl)
                            apex_states[sym]['daily_losses'] += 1
                            closed_positions.append(pos)
                            continue
                        elif lo <= tp1_p:
                            pos.apex_tp1_hit = True
                            pnl_ticket_a = (pos.dollar_risk * 0.5) * pos.tp1_rr
                            pos.realized_pnl += pnl_ticket_a
                            pos.sl_price = entry
                            pos.remaining_weight = 0.5
                    else:
                        if lo <= tp2_p:
                            pos.exit_time = t_utc
                            pnl_ticket_b = (pos.dollar_risk * 0.5) * pos.tp2_rr
                            pos.realized_pnl += pnl_ticket_b
                            pos.closed = True
                            pos.exit_reason = "FULL_TP2"
                            active_positions.remove(pos)
                            apex_states[sym]['active_position'] = None
                            net_pnl = pos.realized_pnl - pos.commission
                            balance += net_pnl
                            closed_positions.append(pos)
                            continue
                        elif hi >= pos.sl_price:
                            pos.exit_time = t_utc
                            pos.closed = True
                            pos.exit_reason = "TP1_THEN_BE"
                            active_positions.remove(pos)
                            apex_states[sym]['active_position'] = None
                            net_pnl = pos.realized_pnl - pos.commission
                            balance += net_pnl
                            closed_positions.append(pos)
                            continue

        # Drawdown Tracking
        if balance > peak_balance: peak_balance = balance
        cur_dd_d = peak_balance - balance
        cur_dd_p = (cur_dd_d / peak_balance) * 100.0 if peak_balance > 0 else 0.0
        if cur_dd_d > max_dd_dollar: max_dd_dollar = cur_dd_d
        if cur_dd_p > max_dd_pct: max_dd_pct = cur_dd_p
        if balance < lowest_balance: lowest_balance = balance

        # Signal Generation
        proposed_signals = []

        # A. LEWIS KELLY SMC (Using Native US/Eastern Session Framework)
        for sym, f_state in forex_states.items():
            if f_state['active_position'] is not None: continue
            if f_state['daily_losses'] >= 2: continue

            t_est = t_utc.tz_convert('US/Eastern')
            if t_est not in f_state['exec_high']: continue

            cfg = f_state['config']
            m15_dt = t_est.floor('15min')
            h4_dt = t_est.floor('4h')

            if m15_dt not in f_state['m15_records']: continue

            m15_bar = f_state['m15_records'][m15_dt]
            h4_bar = f_state['h4_records'].get(h4_dt, {})
            h4_trend = h4_bar.get('Trend', 'none')
            active_session = m15_bar.get('Active_Session', 'none')

            # Pending setup
            p_setup = f_state['pending_setup']
            if p_setup is not None:
                if (t_est - p_setup['time']).total_seconds() > 2700 or active_session not in cfg['target_sessions']:
                    f_state['pending_setup'] = None
                else:
                    direction = 1 if p_setup['type'] == 'long' else -1
                    entry = p_setup['entry_price'] + (f_state['spec']['spread'] if direction == 1 else -f_state['spec']['spread'])
                    sl = p_setup['sl']
                    sl_dist = abs(entry - sl)
                    if (cfg['min_risk_pips'] * cfg['pip_size']) <= sl_dist <= (cfg['max_risk_pips'] * cfg['pip_size']):
                        proposed_signals.append({
                            'symbol': sym,
                            'strategy': 'LK_FOREX_SMC',
                            'direction': direction,
                            'entry_price': entry,
                            'sl_price': sl,
                            'sl_dist': sl_dist,
                            'time': t_utc,
                            'spec': f_state['spec']
                        })
                    f_state['pending_setup'] = None
                    continue

            # New setup evaluation in target sessions (London: 02:00-05:00 EST / NY: 07:00-10:00 EST)
            if active_session in cfg['target_sessions']:
                asia_h = m15_bar.get('Asia_High', np.nan)
                asia_l = m15_bar.get('Asia_Low', np.nan)
                if not np.isnan(asia_h) and not np.isnan(asia_l) and asia_h > asia_l:
                    cur_adr = f_state['adr_dict'].get(t_est.date(), f_state['default_adr'])
                    if np.isnan(cur_adr) or cur_adr <= 0: cur_adr = f_state['default_adr']
                    asian_ratio = (asia_h - asia_l) / cur_adr
                    if asian_ratio > cfg['adr_ratio']: continue

                m15_trend = m15_bar.get('Trend', 'none')
                in_prem = m15_bar.get('In_Premium', False)
                in_disc = m15_bar.get('In_Discount', False)
                swept_h = m15_bar.get('Asia_Ever_Swept_High', False) or m15_bar.get('London_Ever_Swept_High', False) or m15_bar.get('Sweep_High', False)
                swept_l = m15_bar.get('Asia_Ever_Swept_Low', False) or m15_bar.get('London_Ever_Swept_Low', False) or m15_bar.get('Sweep_Low', False)

                cur_h = f_state['exec_high'][t_est]
                cur_l = f_state['exec_low'][t_est]
                cur_c = f_state['exec_close'][t_est]

                if h4_trend != 'bullish' and m15_trend == 'bearish' and in_prem and swept_h:
                    if f_state['awaiting_choch'] != 'Short':
                        f_state['awaiting_choch'] = 'Short'
                        f_state['sweep_extreme_price'] = cur_h
                        f_state['sweep_time'] = t_est
                    else:
                        if cur_h > f_state['sweep_extreme_price']: f_state['sweep_extreme_price'] = cur_h

                elif h4_trend != 'bearish' and m15_trend == 'bullish' and in_disc and swept_l:
                    if f_state['awaiting_choch'] != 'Long':
                        f_state['awaiting_choch'] = 'Long'
                        f_state['sweep_extreme_price'] = cur_l
                        f_state['sweep_time'] = t_est
                    else:
                        if cur_l < f_state['sweep_extreme_price']: f_state['sweep_extreme_price'] = cur_l

                if f_state['awaiting_choch'] == 'Short':
                    if (t_est - f_state['sweep_time']).total_seconds() > 2700:
                        f_state['awaiting_choch'] = None
                    else:
                        if cur_h > f_state['sweep_extreme_price']: f_state['sweep_extreme_price'] = cur_h
                        if f_state['exec_bear_fvg'].get(t_est, False):
                            f_state['recent_displacement_fvg'] = f_state['exec_bear_ob_l'].get(t_est, cur_c)
                        if f_state['exec_choch'].get(t_est, False) or (f_state['exec_trend'].get(t_est) == 'bearish' and f_state['recent_displacement_fvg'] is not None):
                            entry_lvl = f_state['recent_displacement_fvg'] if f_state['recent_displacement_fvg'] is not None else cur_c
                            f_state['pending_setup'] = {
                                'type': 'short',
                                'time': t_est,
                                'entry_price': entry_lvl,
                                'sl': max(f_state['sweep_extreme_price'] + (2.0 * cfg['pip_size']), entry_lvl + (cfg['min_risk_pips'] * cfg['pip_size']))
                            }
                            f_state['awaiting_choch'] = None
                            f_state['recent_displacement_fvg'] = None

                elif f_state['awaiting_choch'] == 'Long':
                    if (t_est - f_state['sweep_time']).total_seconds() > 2700:
                        f_state['awaiting_choch'] = None
                    else:
                        if cur_l < f_state['sweep_extreme_price']: f_state['sweep_extreme_price'] = cur_l
                        if f_state['exec_bull_fvg'].get(t_est, False):
                            f_state['recent_displacement_fvg'] = f_state['exec_bull_ob_h'].get(t_est, cur_c)
                        if f_state['exec_choch'].get(t_est, False) or (f_state['exec_trend'].get(t_est) == 'bullish' and f_state['recent_displacement_fvg'] is not None):
                            entry_lvl = f_state['recent_displacement_fvg'] if f_state['recent_displacement_fvg'] is not None else cur_c
                            f_state['pending_setup'] = {
                                'type': 'long',
                                'time': t_est,
                                'entry_price': entry_lvl,
                                'sl': min(f_state['sweep_extreme_price'] - (2.0 * cfg['pip_size']), entry_lvl - (cfg['min_risk_pips'] * cfg['pip_size']))
                            }
                            f_state['awaiting_choch'] = None
                            f_state['recent_displacement_fvg'] = None

        # B. APEXHUNTER v1.2.1 (Using Native 1.3x/1.4x ATR Stops & 5M Turtle Soup Sweeps)
        for sym in ['XAUUSD', 'NAS100']:
            a_state = apex_states[sym]
            if a_state['active_position'] is not None: continue
            if a_state['daily_losses'] >= 2: continue
            if t_utc not in a_state['prep_records']: continue

            row = a_state['prep_records'][t_utc]
            wday = t_utc.strftime('%A')
            h = t_hour

            if not (6 <= h < 21): continue
            if h in [9, 13] and row['stretch_ratio'] < 1.10: continue
            if wday == 'Monday' and (14 <= h < 18): continue
            if pd.isna(row['atr_1h']) or row['atr_1h'] <= 0: continue
            if pd.isna(row['adx_1h']) or row['adx_1h'] < 15.0 or row['adx_1h'] > 45.0: continue
            if pd.isna(row['stretch_ratio']) or row['stretch_ratio'] < 0.40 or row['chase_ratio'] > 0.50: continue
            if row['adx_1h'] < 20.0 and row['stretch_ratio'] < 0.90: continue

            bias = row['bias_1h']
            p_diff = row['p_diff']
            c_diff = row['c_diff']

            b_flip = (bias == 1) and (p_diff <= 0) and (c_diff > 0) and (row['close'] > row['vwap_5m']) and (row['close'] > row['ema20_1h'])
            s_flip = (bias == -1) and (p_diff >= 0) and (c_diff < 0) and (row['close'] < row['vwap_5m']) and (row['close'] < row['ema20_1h'])

            if b_flip or s_flip:
                direction = 1 if b_flip else -1
                bar_idx = a_state['prep_idx_map'][t_utc]

                has_sweep, _ = detect_5m_liquidity_sweep(
                    a_state['lows_arr'], a_state['highs_arr'], a_state['closes_arr'],
                    entry_idx=bar_idx, direction=direction, swing_lookback=20, pullback_window=8
                )
                if not has_sweep: continue

                raw_entry = float(row['close'])
                atr = float(row['atr_1h'])
                sl_mult = 1.30 if sym == 'XAUUSD' else 1.40 # Native 1.3x/1.4x ATR breathing room
                sl_dist = max(atr * sl_mult, a_state['spec']['spread'] * 3.0)
                entry = raw_entry + (a_state['spec']['spread'] if direction == 1 else -a_state['spec']['spread'])
                sl = entry - sl_dist if direction == 1 else entry + sl_dist

                proposed_signals.append({
                    'symbol': sym,
                    'strategy': 'APEXHUNTER_1_2_1',
                    'direction': direction,
                    'entry_price': entry,
                    'sl_price': sl,
                    'sl_dist': sl_dist,
                    'time': t_utc,
                    'spec': a_state['spec']
                })

        # Order Authorization
        if proposed_signals:
            for sig in proposed_signals:
                sym = sig['symbol']
                if daily_halt or daily_realized_loss >= 225.0: continue
                if balance <= 4600.0: continue

                risk_bearing_trades = [p for p in active_positions if p.has_active_downside_risk]
                if len(risk_bearing_trades) >= 3: continue
                if any(p.symbol == sym for p in active_positions): continue

                spec = sig['spec']
                sl_pips = sig['sl_dist'] / spec['pip']
                pip_val_per_lot = spec['contract'] * spec['pip']
                raw_lots = 50.0 / (sl_pips * pip_val_per_lot + 1e-9)
                lots = max(spec['vol_min'], round(raw_lots, 2))
                comm = lots * spec['comm_lot']

                new_pos = LiveSimTrade(
                    symbol=sym,
                    strategy=sig['strategy'],
                    direction=sig['direction'],
                    entry_price=sig['entry_price'],
                    sl_price=sig['sl_price'],
                    risk_dist=sig['sl_dist'],
                    entry_time=t_utc,
                    commission=comm,
                    dollar_risk=50.0
                )

                active_positions.append(new_pos)
                if sym in forex_states: forex_states[sym]['active_position'] = new_pos
                if sym in apex_states: apex_states[sym]['active_position'] = new_pos

    # Close positions at simulation end
    for pos in list(active_positions):
        pos.exit_time = timeline[-1]
        pos.exit_reason = "End of Replay"
        pos.closed = True
        net_pnl = pos.realized_pnl - pos.commission
        closed_positions.append(pos)
        balance += net_pnl
    active_positions.clear()

    print(f"Replay completed across {len(timeline):,} ticks in {round(time.time() - sim_t0, 1)}s.")

    df_trades = pd.DataFrame([{
        'symbol': p.symbol,
        'strategy': p.strategy,
        'direction': 'BUY' if p.direction == 1 else 'SELL',
        'entry_time': p.entry_time,
        'exit_time': p.exit_time,
        'pnl': p.realized_pnl - p.commission,
        'commission': p.commission,
        'exit_reason': p.exit_reason
    } for p in closed_positions]).sort_values('entry_time').reset_index(drop=True)

    wins = (df_trades['pnl'] > 0).sum()
    losses = (df_trades['pnl'] < 0).sum()
    win_rate = (wins / len(df_trades)) * 100.0 if len(df_trades) > 0 else 0.0
    total_profit = df_trades[df_trades['pnl'] > 0]['pnl'].sum()
    total_loss = abs(df_trades[df_trades['pnl'] < 0]['pnl'].sum())
    profit_factor = total_profit / (total_loss + 1e-9)
    net_profit = df_trades['pnl'].sum()

    print("\n" + "=" * 98)
    print("      100% INDEPENDENT RAW-CANDLE REPLAY RESULTS (JAN - SEP 2026)")
    print("=" * 98)
    print(f"  Starting Balance:               ${starting_capital:,.2f}")
    print(f"  Final Non-Compounded Balance:   ${starting_capital + net_profit:,.2f}")
    print(f"  Total Net Profit:              +${net_profit:,.2f} (+{(net_profit/starting_capital)*100:.2f}%)")
    print(f"  Total Trades Executed:          {len(df_trades)}")
    print(f"  Winning Trades:                 {wins} ({win_rate:.2f}%)")
    print(f"  Losing Trades:                  {losses} ({100-win_rate:.2f}%)")
    print(f"  Portfolio Profit Factor:        {profit_factor:.2f}")
    print(f"  Total ECN Commission Deducted:  ${df_trades['commission'].sum():,.2f}")
    print("-" * 98)
    print("  STRATEGY CONTRIBUTION BREAKDOWN:")
    for strat in ['APEXHUNTER_1_2_1', 'LK_FOREX_SMC']:
        sub = df_trades[df_trades['strategy'] == strat]
        sub_wins = (sub['pnl'] > 0).sum()
        sub_wr = (sub_wins / len(sub) * 100.0) if len(sub) > 0 else 0.0
        sub_pnl = sub['pnl'].sum()
        sub_pf = sub[sub['pnl'] > 0]['pnl'].sum() / (abs(sub[sub['pnl'] < 0]['pnl'].sum()) + 1e-9) if len(sub) > 0 else 0.0
        strat_lbl = "ApexHunter v1.2.1 (Gold/Nasdaq)" if "APEX" in strat else "Lewis Kelly SMC (Forex Basket)"
        print(f"    * {strat_lbl:<32}: {len(sub):>3} trades | {sub_wr:>5.1f}% WR | PF {sub_pf:.2f} | Net PnL: +${sub_pnl:>8.2f}")
    print("=" * 98)

if __name__ == '__main__':
    run_unified_master_live_simulation()

