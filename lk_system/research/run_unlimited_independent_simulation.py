"""
========================================================================================
UNLIMITED SLOT & UNCONSTRAINED RISK INDEPENDENT MASTER LIVE SIMULATION
========================================================================================
Purpose:
- Test 100% independent raw candle live simulation with:
  1. UNLIMITED SLOTS (No 3-slot cap; any valid setup executes).
  2. NO CIRCUIT BREAKER (No daily loss halting; trades unconstrained).
  3. Capture Max Daily Drawdown ($ and %) and Max Equity Drawdown ($ and %).
  4. Track Maximum Concurrent Open Positions reached at any point.
  5. 100% Production-Locked DCC v1.2.1 (No accidental experimental sweep filter).
  6. 100% Lewis Kelly SMC (US/Eastern sessions, 3-tier scaling + BE).
  7. Exact ECN commissions ($5-$7/lot) + realistic spreads deducted on every tick.
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
if PROJECT_ROOT not in sys.path: sys.path.insert(0, PROJECT_ROOT)
if PROJECT_ROOT not in sys.path: sys.path.insert(0, PROJECT_ROOT)
if DC_ROOT not in sys.path: sys.path.insert(0, DC_ROOT)
if os.path.join(DC_ROOT, 'v1.2') not in sys.path: sys.path.insert(0, os.path.join(DC_ROOT, 'v1.2'))

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

class LiveSimTrade:
    def __init__(self, symbol: str, strategy: str, direction: int, entry_price: float,
                 sl_price: float, risk_dist: float, entry_time: datetime,
                 commission: float, dollar_risk: float = 50.0, tp1_rr: float = 1.40, tp2_rr: float = 2.20):
        self.symbol = symbol
        self.strategy = strategy
        self.direction = direction
        self.entry_price = entry_price
        self.sl_price = sl_price
        self.risk_dist = risk_dist
        self.entry_time = entry_time
        self.commission = commission
        self.dollar_risk = dollar_risk
        self.exit_time = None
        self.exit_price = None
        self.exit_reason = None
        self.realized_pnl = 0.0
        self.remaining_weight = 1.0
        self.closed = False
        
        # SMC Trailing
        self.lk_tier_3r = False
        self.lk_tier_5r = False
        
        # ApexHunter Native Dual-Ticket
        self.apex_tp1_hit = False
        self.apex_tp2_hit = False
        self.tp1_rr = tp1_rr
        self.tp2_rr = tp2_rr
        
    @property
    def has_active_downside_risk(self) -> bool:
        if self.closed:
            return False
        if self.strategy == 'LK_FOREX_SMC':
            return not self.lk_tier_3r
        else:
            return not self.apex_tp1_hit


def load_all_market_data():
    print("[1/4] Ingesting and preparing 100% RAW Candle Data across all 8 Assets...")
    t0 = time.time()
    market_data = {}

    # 1. Lewis Kelly SMC Forex Pairs
    for sym, cfg in FOREX_CONFIGS.items():
        exec_tf = cfg['tf_exec']
        df_h4 = pd.read_parquet(os.path.join(DATA_DIR, f"{sym}_H4_2026.parquet"))
        df_m15 = pd.read_parquet(os.path.join(DATA_DIR, f"{sym}_M15_2026.parquet"))
        df_exec = pd.read_parquet(os.path.join(DATA_DIR, f"{sym}_{exec_tf}_2026.parquet"))

        for df in [df_h4, df_m15, df_exec]:
            if df.index.tz is None:
                df.index = df.index.tz_localize('UTC').tz_convert('US/Eastern')
            else:
                df.index = df.index.tz_convert('US/Eastern')

        df_d1 = df_m15.resample('1D').agg({'High': 'max', 'Low': 'min'}).dropna()
        df_d1['Range'] = df_d1['High'] - df_d1['Low']
        df_d1['ADR_5'] = df_d1['Range'].rolling(5).mean().shift(1)
        adr_dict = df_d1['ADR_5'].dropna().to_dict()
        default_adr = float(df_d1['Range'].mean()) if len(df_d1) > 0 else 0.0050

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

        exec_utc_index = df_exec.index.tz_convert('UTC')

        market_data[sym] = {
            'type': 'FOREX_SMC',
            'config': cfg,
            'spec': SPECS[sym],
            'adr_dict': adr_dict,
            'default_adr': default_adr,
            'h4_records': df_h4.to_dict('index'),
            'm15_records': df_m15.to_dict('index'),
            'exec_utc_index': exec_utc_index,
            'exec_high': df_exec['High'].to_dict(),
            'exec_low': df_exec['Low'].to_dict(),
            'exec_close': df_exec['Close'].to_dict(),
            'exec_choch': df_exec['CHoCH'].to_dict() if 'CHoCH' in df_exec else {},
            'exec_trend': df_exec['Trend'].to_dict() if 'Trend' in df_exec else {},
            'exec_bear_fvg': df_exec['Bearish_FVG'].to_dict() if 'Bearish_FVG' in df_exec else {},
            'exec_bull_fvg': df_exec['Bullish_FVG'].to_dict() if 'Bullish_FVG' in df_exec else {},
            'exec_bear_ob_l': df_exec['Bearish_OB_Low'].to_dict() if 'Bearish_OB_Low' in df_exec else {},
            'exec_bull_ob_h': df_exec['Bullish_OB_High'].to_dict() if 'Bullish_OB_High' in df_exec else {},
        }
        print(f"  Loaded {sym:<6} (Forex SMC, {len(df_exec):,} bars, native US/Eastern)")

    # 2. DCC ApexHunter v1.2.1 (XAUUSD & NAS100)
    broker_offset = timedelta(hours=3)
    for sym in ['XAUUSD', 'NAS100']:
        df_m5 = pd.read_parquet(os.path.join(DC_CACHE, f"m5_bars_{sym}.parquet"))
        df_m5['time_utc'] = (pd.to_datetime(df_m5['time'], unit='s', utc=True) - broker_offset).astype('datetime64[us, UTC]')
        df_m5.set_index('time_utc', inplace=True)
        df_m5.rename(columns={'tick_volume': 'volume'}, inplace=True)

        df_h1 = pd.read_parquet(os.path.join(DC_CACHE, f"h1_bars_{sym}.parquet"))
        df_h1['time_utc'] = (pd.to_datetime(df_h1['time'], unit='s', utc=True) - broker_offset).astype('datetime64[us, UTC]')
        df_h1.set_index('time_utc', inplace=True)

        sl_mult = 0.90 if sym == 'XAUUSD' else 1.00
        tp1_rr = 1.40 if sym == 'XAUUSD' else 1.50
        tp2_rr = 2.20 if sym == 'XAUUSD' else 2.00

        engine = DCCEngineV12(symbol=sym, atr_sl_multiplier=sl_mult, tp1_rr=tp1_rr, tp2_rr=tp2_rr)
        df_prep = engine.prepare_data(df_m5, df_h1)
        df_prep['p_diff'] = (df_prep['ema9_5m'] - df_prep['ema20_5m']).shift(1)
        df_prep['c_diff'] = df_prep['ema9_5m'] - df_prep['ema20_5m']

        market_data[sym] = {
            'type': 'APEX_HUNTER_1_2_1',
            'spec': SPECS[sym],
            'sl_mult': sl_mult,
            'tp1_rr': tp1_rr,
            'tp2_rr': tp2_rr,
            'df_prep': df_prep,
            'prep_records': df_prep.to_dict('index'),
        }
        print(f"  Loaded {sym:<6} (DCC v1.2.1, {len(df_prep):,} bars, SL={sl_mult}x ATR, TP1={tp1_rr}R, TP2={tp2_rr}R)")

    print(f"Data ingestion completed in {round(time.time() - t0, 1)}s.")
    return market_data


def run_unlimited_master_simulation():
    market_data = load_all_market_data()

    print("\n[2/4] Constructing Unified Master Clock across all 8 Assets...")
    all_utc_timestamps = set()
    for sym, data in market_data.items():
        if data['type'] == 'FOREX_SMC':
            all_utc_timestamps.update(data['exec_utc_index'])
        else:
            all_utc_timestamps.update(data['df_prep'].index)

    # Full period where Gold & Nasdaq have data: Jan 01, 2026 to Sep 18, 2026
    timeline = sorted([t for t in all_utc_timestamps if t.year == 2026 and t <= pd.Timestamp('2026-09-18 23:59:59', tz='UTC')])
    print(f"  Master Clock established: {len(timeline):,} active bars from {timeline[0]} to {timeline[-1]}")

    # Pre-compute time structures for high-speed replay
    print("  Pre-computing timezone lookups...")
    timeline_est = [t.tz_convert('US/Eastern') for t in timeline]
    timeline_dates = [t.date() for t in timeline]
    timeline_hours = [t.hour for t in timeline]
    timeline_wdays = [t.strftime('%A') for t in timeline]

    print("\n[3/4] Initializing Strategy Trackers...")
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
                'current_session': None,
                'asian_range_pips': None,
                'asian_high': None,
                'asian_low': None,
                'asian_valid': False,
                'current_day': None,
            }

    apex_states = {}
    for sym in ['XAUUSD', 'NAS100']:
        data = market_data[sym]
        apex_states[sym] = {
            'spec': data['spec'],
            'sl_mult': data['sl_mult'],
            'tp1_rr': data['tp1_rr'],
            'tp2_rr': data['tp2_rr'],
            'prep_records': data['prep_records'],
            'active_position': None,
        }

    # Simulation Accounts & Metrics
    starting_capital = 5000.0
    balance = starting_capital
    peak_balance = starting_capital
    max_equity_dd_dollars = 0.0
    max_equity_dd_pct = 0.0

    current_sim_day = None
    day_open_balance = starting_capital
    day_realized_loss = 0.0
    max_daily_loss_dollars = 0.0
    max_daily_loss_pct = 0.0

    max_concurrent_positions = 0
    concurrent_counts = []

    active_positions: List[LiveSimTrade] = []
    closed_positions: List[LiveSimTrade] = []

    print("\n[4/4] Executing UNLIMITED-SLOT Raw-Candle Live Simulation Replay...")
    sim_t0 = time.time()

    for idx, t_utc in enumerate(timeline):
        t_est = timeline_est[idx]
        t_date = timeline_dates[idx]
        t_hour = timeline_hours[idx]
        t_wday = timeline_wdays[idx]

        # Daily Tracker Reset at midnight
        if current_sim_day != t_date:
            if current_sim_day is not None:
                daily_drop = max(0.0, day_open_balance - balance)
                daily_pct = (daily_drop / day_open_balance) * 100.0
                if daily_drop > max_daily_loss_dollars:
                    max_daily_loss_dollars = daily_drop
                    max_daily_loss_pct = daily_pct
            current_sim_day = t_date
            day_open_balance = balance

        # Track Concurrency
        n_active = len(active_positions)
        if n_active > max_concurrent_positions:
            max_concurrent_positions = n_active
        concurrent_counts.append(n_active)

        # -------------------------------------------------------------
        # A. Position Management (Tick by Tick Evaluation)
        # -------------------------------------------------------------
        for pos in list(active_positions):
            sym = pos.symbol
            hi = None
            lo = None

            if sym in forex_states:
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

            # 1. Lewis Kelly SMC Management (+3R 30%+BE, +5R 30%, +8R 40% runner)
            if pos.strategy == 'LK_FOREX_SMC':
                if pos.direction == 1: # Long
                    peak_r = (hi - entry) / risk if risk > 0 else 0.0

                    if peak_r >= 3.0 and not pos.lk_tier_3r:
                        pos.lk_tier_3r = True
                        pnl_tier = pos.dollar_risk * 0.30 * 3.0
                        pos.realized_pnl += pnl_tier
                        pos.remaining_weight -= 0.30
                        pos.sl_price = entry + (0.2 * risk) # Trailed to BE + buffer

                    if peak_r >= 5.0 and not pos.lk_tier_5r:
                        pos.lk_tier_5r = True
                        pnl_tier = pos.dollar_risk * 0.30 * 5.0
                        pos.realized_pnl += pnl_tier
                        pos.remaining_weight -= 0.30

                    if lo <= pos.sl_price:
                        pos.exit_time = t_utc
                        pos.exit_price = pos.sl_price
                        rem_r = (pos.sl_price - entry) / risk
                        rem_pnl = pos.dollar_risk * pos.remaining_weight * rem_r
                        pos.realized_pnl += rem_pnl
                        pos.closed = True
                        pos.exit_reason = "LK Tier 1 + BE Stopped" if pos.lk_tier_3r else "LK Full SL Hit"
                        active_positions.remove(pos)
                        forex_states[sym]['active_position'] = None
                        net_pnl = pos.realized_pnl - pos.commission
                        balance += net_pnl
                        closed_positions.append(pos)
                        continue

                    if hi >= entry + (8.0 * risk):
                        pos.exit_time = t_utc
                        pos.exit_price = entry + (8.0 * risk)
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
                        pos.exit_price = pos.sl_price
                        rem_r = (entry - pos.sl_price) / risk
                        rem_pnl = pos.dollar_risk * pos.remaining_weight * rem_r
                        pos.realized_pnl += rem_pnl
                        pos.closed = True
                        pos.exit_reason = "LK Tier 1 + BE Stopped" if pos.lk_tier_3r else "LK Full SL Hit"
                        active_positions.remove(pos)
                        forex_states[sym]['active_position'] = None
                        net_pnl = pos.realized_pnl - pos.commission
                        balance += net_pnl
                        closed_positions.append(pos)
                        continue

                    if lo <= entry - (8.0 * risk):
                        pos.exit_time = t_utc
                        pos.exit_price = entry - (8.0 * risk)
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

            # 2. ApexHunter v1.2.1 Native Dual-Ticket (TP1 50% + BE, TP2 50%)
            elif pos.strategy == 'APEXHUNTER_1_2_1':
                tp1_p = entry + (pos.risk_dist * pos.tp1_rr) if pos.direction == 1 else entry - (pos.risk_dist * pos.tp1_rr)
                tp2_p = entry + (pos.risk_dist * pos.tp2_rr) if pos.direction == 1 else entry - (pos.risk_dist * pos.tp2_rr)

                if pos.direction == 1: # Long
                    if not pos.apex_tp1_hit and hi >= tp1_p:
                        pos.apex_tp1_hit = True
                        pos.realized_pnl += pos.dollar_risk * 0.50 * pos.tp1_rr
                        pos.remaining_weight = 0.50
                        pos.sl_price = entry # BE Shift

                    if pos.apex_tp1_hit and hi >= tp2_p:
                        pos.apex_tp2_hit = True
                        pos.realized_pnl += pos.dollar_risk * 0.50 * pos.tp2_rr
                        pos.exit_time = t_utc
                        pos.closed = True
                        pos.exit_reason = "FULL_TP2"
                        active_positions.remove(pos)
                        apex_states[sym]['active_position'] = None
                        net_pnl = pos.realized_pnl - pos.commission
                        balance += net_pnl
                        closed_positions.append(pos)
                        continue

                    if lo <= pos.sl_price:
                        pos.exit_time = t_utc
                        pos.closed = True
                        if not pos.apex_tp1_hit:
                            pos.realized_pnl = -pos.dollar_risk
                            pos.exit_reason = "FULL_SL"
                        else:
                            pos.exit_reason = "TP1_THEN_BE"
                        active_positions.remove(pos)
                        apex_states[sym]['active_position'] = None
                        net_pnl = pos.realized_pnl - pos.commission
                        balance += net_pnl
                        closed_positions.append(pos)
                        continue

                else: # Short
                    if not pos.apex_tp1_hit and lo <= tp1_p:
                        pos.apex_tp1_hit = True
                        pos.realized_pnl += pos.dollar_risk * 0.50 * pos.tp1_rr
                        pos.remaining_weight = 0.50
                        pos.sl_price = entry

                    if pos.apex_tp1_hit and lo <= tp2_p:
                        pos.apex_tp2_hit = True
                        pos.realized_pnl += pos.dollar_risk * 0.50 * pos.tp2_rr
                        pos.exit_time = t_utc
                        pos.closed = True
                        pos.exit_reason = "FULL_TP2"
                        active_positions.remove(pos)
                        apex_states[sym]['active_position'] = None
                        net_pnl = pos.realized_pnl - pos.commission
                        balance += net_pnl
                        closed_positions.append(pos)
                        continue

                    if hi >= pos.sl_price:
                        pos.exit_time = t_utc
                        pos.closed = True
                        if not pos.apex_tp1_hit:
                            pos.realized_pnl = -pos.dollar_risk
                            pos.exit_reason = "FULL_SL"
                        else:
                            pos.exit_reason = "TP1_THEN_BE"
                        active_positions.remove(pos)
                        apex_states[sym]['active_position'] = None
                        net_pnl = pos.realized_pnl - pos.commission
                        balance += net_pnl
                        closed_positions.append(pos)
                        continue

        # Track Peak & Equity Drawdown
        if balance > peak_balance:
            peak_balance = balance
        eq_dd = peak_balance - balance
        eq_dd_pct = (eq_dd / peak_balance) * 100.0
        if eq_dd > max_equity_dd_dollars:
            max_equity_dd_dollars = eq_dd
            max_equity_dd_pct = eq_dd_pct

        # -------------------------------------------------------------
        # B. Signal Evaluation (Simultaneous across all assets)
        # -------------------------------------------------------------
        proposed_signals = []

        # 1. LEWIS KELLY SMC
        for sym, f_state in forex_states.items():
            if f_state['active_position'] is not None: continue

            d_est = t_est.date()
            if f_state['current_day'] != d_est:
                f_state['current_day'] = d_est
                f_state['asian_range_pips'] = None
                f_state['asian_high'] = None
                f_state['asian_low'] = None
                f_state['asian_valid'] = False
                f_state['pending_setup'] = None
                f_state['awaiting_choch'] = None

            # Asian Session: 19:00 - 01:00 EST
            if (t_est.hour >= 19 or t_est.hour < 1):
                c_high = f_state['exec_high'].get(t_est)
                c_low = f_state['exec_low'].get(t_est)
                if c_high is not None and c_low is not None:
                    if f_state['asian_high'] is None or c_high > f_state['asian_high']:
                        f_state['asian_high'] = c_high
                    if f_state['asian_low'] is None or c_low < f_state['asian_low']:
                        f_state['asian_low'] = c_low

            # Asian Close & ADR Validation at 01:00 EST
            if t_est.hour == 1 and t_est.minute == 0 and not f_state['asian_valid'] and f_state['asian_high'] is not None:
                pip = f_state['config']['pip_size']
                rng_pips = (f_state['asian_high'] - f_state['asian_low']) / pip
                f_state['asian_range_pips'] = rng_pips
                d_prev = d_est - timedelta(days=1)
                adr_val = f_state['adr_dict'].get(pd.Timestamp(d_prev), f_state['default_adr'])
                adr_pips = adr_val / pip
                ratio = rng_pips / adr_pips if adr_pips > 0 else 0.40
                max_rat = 0.65 if sym in ['USDJPY', 'EURJPY'] else 0.45
                if 0.25 <= ratio <= max_rat:
                    f_state['asian_valid'] = True

            # Target Killzones: London (02:00-05:00 EST) & NY (07:00-10:00 EST)
            active_session = None
            if 2 <= t_est.hour < 5: active_session = 'London'
            elif 7 <= t_est.hour < 10: active_session = 'NY'

            if active_session in f_state['config']['target_sessions'] and f_state['asian_valid']:
                cur_close = f_state['exec_close'].get(t_est)
                cur_high = f_state['exec_high'].get(t_est)
                cur_low = f_state['exec_low'].get(t_est)

                if cur_close is not None and cur_high is not None and cur_low is not None:
                    # 1. Sweep detection
                    if f_state['pending_setup'] is None:
                        if cur_high > f_state['asian_high']:
                            f_state['pending_setup'] = 'BEARISH_SWEEP'
                            f_state['sweep_extreme_price'] = cur_high
                            f_state['awaiting_choch'] = True
                        elif cur_low < f_state['asian_low']:
                            f_state['pending_setup'] = 'BULLISH_SWEEP'
                            f_state['sweep_extreme_price'] = cur_low
                            f_state['awaiting_choch'] = True
                    else:
                        if f_state['pending_setup'] == 'BEARISH_SWEEP' and cur_high > f_state['sweep_extreme_price']:
                            f_state['sweep_extreme_price'] = cur_high
                        elif f_state['pending_setup'] == 'BULLISH_SWEEP' and cur_low < f_state['sweep_extreme_price']:
                            f_state['sweep_extreme_price'] = cur_low

                    # 2. CHoCH Confirmation
                    if f_state['awaiting_choch']:
                        choch_sig = f_state['exec_choch'].get(t_est, 0)
                        if f_state['pending_setup'] == 'BEARISH_SWEEP' and choch_sig == -1:
                            f_state['awaiting_choch'] = False
                        elif f_state['pending_setup'] == 'BULLISH_SWEEP' and choch_sig == 1:
                            f_state['awaiting_choch'] = False

                    # 3. Retest & Execution
                    if f_state['pending_setup'] and not f_state['awaiting_choch']:
                        sig = None
                        if f_state['pending_setup'] == 'BEARISH_SWEEP':
                            has_fvg = f_state['exec_bear_fvg'].get(t_est, False)
                            if has_fvg or cur_close < f_state['sweep_extreme_price']:
                                sig = 'SELL'
                        elif f_state['pending_setup'] == 'BULLISH_SWEEP':
                            has_fvg = f_state['exec_bull_fvg'].get(t_est, False)
                            if has_fvg or cur_close > f_state['sweep_extreme_price']:
                                sig = 'BUY'

                        if sig:
                            pip = f_state['config']['pip_size']
                            spread_pips = f_state['config']['spread_pips']
                            if sig == 'SELL':
                                sl = f_state['sweep_extreme_price'] + (spread_pips * pip)
                                risk_dist = sl - cur_close
                                if f_state['config']['min_risk_pips'] <= (risk_dist / pip) <= f_state['config']['max_risk_pips']:
                                    proposed_signals.append({
                                        'symbol': sym,
                                        'strategy': 'LK_FOREX_SMC',
                                        'direction': -1,
                                        'entry_price': cur_close,
                                        'sl_price': sl,
                                        'sl_dist': risk_dist,
                                        'spec': f_state['spec']
                                    })
                            else:
                                sl = f_state['sweep_extreme_price'] - (spread_pips * pip)
                                risk_dist = cur_close - sl
                                if f_state['config']['min_risk_pips'] <= (risk_dist / pip) <= f_state['config']['max_risk_pips']:
                                    proposed_signals.append({
                                        'symbol': sym,
                                        'strategy': 'LK_FOREX_SMC',
                                        'direction': 1,
                                        'entry_price': cur_close,
                                        'sl_price': sl,
                                        'sl_dist': risk_dist,
                                        'spec': f_state['spec']
                                    })
                            f_state['pending_setup'] = None

        # 2. DCC APEXHUNTER v1.2.1 (Pure Locked TripleGuard Specification)
        for sym in ['XAUUSD', 'NAS100']:
            a_state = apex_states[sym]
            if a_state['active_position'] is not None: continue
            if t_utc not in a_state['prep_records']: continue

            row = a_state['prep_records'][t_utc]
            h = t_hour
            wday = t_wday

            # Trading Hours: 06:00 to 19:00 UTC
            if not (6 <= h < 19): continue

            # Smart Killzone: 09:00 & 13:00 UTC require Stretch >= 1.10x
            stretch = row['stretch_ratio']
            if h in [9, 13] and (pd.isna(stretch) or stretch < 1.10): continue

            # Monday PM Block: 14:00 to 18:00 UTC
            if wday == 'Monday' and (14 <= h < 18): continue

            # Indicator validation
            if pd.isna(row['atr_1h']) or row['atr_1h'] <= 0: continue
            if pd.isna(row['adx_1h']) or row['adx_1h'] < 15.0 or row['adx_1h'] > 45.0: continue
            if pd.isna(row['stretch_ratio']) or row['stretch_ratio'] < 0.40 or row['chase_ratio'] > 0.50: continue

            # Shield 4: Low-ADX Quality Gate (v1.2.1-ApexHunter)
            if row['adx_1h'] < 20.0 and row['stretch_ratio'] < 0.90: continue

            # 5M EMA Flip & VWAP / 1H EMA20 alignment
            bias = row['bias_1h']
            p_diff = row['p_diff']
            c_diff = row['c_diff']

            b_flip = (bias == 1) and (p_diff <= 0) and (c_diff > 0) and (row['close'] > row['vwap_5m']) and (row['close'] > row['ema20_1h'])
            s_flip = (bias == -1) and (p_diff >= 0) and (c_diff < 0) and (row['close'] < row['vwap_5m']) and (row['close'] < row['ema20_1h'])

            if b_flip or s_flip:
                direction = 1 if b_flip else -1
                raw_entry = float(row['close'])
                atr = float(row['atr_1h'])
                sl_dist = atr * a_state['sl_mult']
                entry = raw_entry + (a_state['spec']['spread'] if direction == 1 else -a_state['spec']['spread'])
                sl = entry - sl_dist if direction == 1 else entry + sl_dist

                proposed_signals.append({
                    'symbol': sym,
                    'strategy': 'APEXHUNTER_1_2_1',
                    'direction': direction,
                    'entry_price': entry,
                    'sl_price': sl,
                    'sl_dist': sl_dist,
                    'spec': a_state['spec'],
                    'tp1_rr': a_state['tp1_rr'],
                    'tp2_rr': a_state['tp2_rr'],
                })

        # -------------------------------------------------------------
        # C. Order Execution (UNLIMITED SLOTS, NO CB HALT)
        # -------------------------------------------------------------
        if proposed_signals:
            for sig in proposed_signals:
                sym = sig['symbol']
                # Max 1 active position per individual symbol
                if any(p.symbol == sym for p in active_positions):
                    continue

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
                    dollar_risk=50.0,
                    tp1_rr=sig.get('tp1_rr', 1.40),
                    tp2_rr=sig.get('tp2_rr', 2.20),
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

    elapsed = time.time() - sim_t0
    print(f"Replay completed across {len(timeline):,} ticks in {round(elapsed, 1)}s.")

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

    df_trades.to_csv('backtest/unlimited_slot_independent_simulation_trades.csv', index=False)

    wins = (df_trades['pnl'] > 0).sum()
    losses = (df_trades['pnl'] < 0).sum()
    win_rate = (wins / len(df_trades)) * 100.0 if len(df_trades) > 0 else 0.0
    total_profit = df_trades[df_trades['pnl'] > 0]['pnl'].sum()
    total_loss = abs(df_trades[df_trades['pnl'] < 0]['pnl'].sum())
    profit_factor = total_profit / (total_loss + 1e-9)
    net_profit = df_trades['pnl'].sum()

    print("\n" + "=" * 98)
    print("      UNLIMITED-SLOT & UNCONSTRAINED RISK INDEPENDENT LIVE SIMULATION")
    print("      (Jan 01, 2026 – Sep 18, 2026 | 100% Raw Candle Bars | Zero Old CSV Logs)")
    print("=" * 98)
    print(f"  Starting Balance:               ${starting_capital:,.2f}")
    print(f"  Final Balance:                  ${starting_capital + net_profit:,.2f}")
    print(f"  Total Net Profit:              +${net_profit:,.2f} (+{(net_profit/starting_capital)*100:.2f}%)")
    print(f"  Total Trades Executed:          {len(df_trades)}")
    print(f"  Winning Trades:                 {wins} ({win_rate:.2f}%)")
    print(f"  Losing Trades:                  {losses} ({100-win_rate:.2f}%)")
    print(f"  Portfolio Profit Factor:        {profit_factor:.2f}")
    print(f"  Total ECN Commission Deducted:  ${df_trades['commission'].sum():,.2f}")
    print("-" * 98)
    print("  DRAWDOWN & CONCURRENCY RISK AUDIT:")
    print(f"  * Maximum Equity Drawdown:      ${max_equity_dd_dollars:,.2f} ({max_equity_dd_pct:.2f}%)")
    print(f"  * Worst Single-Day Drop:        ${max_daily_loss_dollars:,.2f} ({max_daily_loss_pct:.2f}%)")
    print(f"  * Peak Concurrent Open Trades:  {max_concurrent_positions} simultaneous positions")
    print(f"  * Avg Concurrent Open Trades:   {np.mean(concurrent_counts):.2f} positions")
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
    run_unlimited_master_simulation()
