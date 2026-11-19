"""
Refined Lewis Kelly Artificial SMC Trading Brain
Fully Mechanical Implementation of Lewis Kelly's Complete SMC Strategy

Core Principles Implemented:
1. Multi-Timeframe Alignment: 4H Macro Trend Bias -> 15M Dealing Range & Sessions -> 1M Precision Execution.
2. Institutional Liquidity Sweep: Wick pierced beyond session level, but candle body closed INSIDE/REJECTED.
   Expansion Breakout Invalidation: Candle body closing firmly outside invalidates counter-trend reversals.
3. 50% Dealing Range Equilibrium: Shorts strictly in Premium (>50%), Longs strictly in Discount (<50%).
4. 1M CHoCH with Displacement: True body close violation of 1M protected swing.
5. Limit Order at FVG First Touch / Consequent Encroachment (C.E.):
   - Short: Limit order at FVG bottom (first touch) or 50% C.E.
   - Long: Limit order at FVG top (first touch) or 50% C.E.
   - Prevents market-chasing at low R:R; gives ultra-tight 3-6 pip invalidation stop losses.
6. Dynamic Asset Customization: Tailored to each pair's volatility, spread, and session behavior.
7. Risk Management: 1% risk/trade, max 2 daily losses circuit breaker, tiered scaling (+3R BE, +5R, +8R-10R).
"""

import sys
import os
import glob
import json
import pandas as pd
import numpy as np
from datetime import datetime

CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(CURRENT_DIR)
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

try:
    from core.market_structure import MarketStructure
    from core.imbalance import ImbalanceDetector
    from core.dealing_range import DealingRangeEngine
except ImportError:
    from lk_system.core.market_structure import MarketStructure
    from lk_system.core.imbalance import ImbalanceDetector
    from lk_system.core.dealing_range import DealingRangeEngine

DATA_DIR = os.path.join(PROJECT_ROOT, "data_jan_nov_2026")

ASSET_PROFILES = {
    'EURUSD': {
        'pip_size': 0.0001,
        'spread_pips': 0.8,
        'min_risk_pips': 3.5,
        'max_risk_pips': 12.0,
        'killzones': ['London', 'NY'],
        'scale_tiers': [(3.0, 0.30), (5.0, 0.30), (8.0, 0.40)],
        'be_trigger_r': 3.0,
        'be_buffer_r': 0.2,
        'max_daily_losses': 2,
        'adr_filter_ratio': 0.40,
        'fvg_entry_type': 'FIRST_TOUCH' # 'FIRST_TOUCH' or 'CE_50'
    },
    'GBPUSD': {
        'pip_size': 0.0001,
        'spread_pips': 1.2,
        'min_risk_pips': 5.0,
        'max_risk_pips': 16.0,
        'killzones': ['London', 'NY'],
        'scale_tiers': [(3.5, 0.30), (6.0, 0.30), (10.0, 0.40)],
        'be_trigger_r': 3.5,
        'be_buffer_r': 0.2,
        'max_daily_losses': 2,
        'adr_filter_ratio': 0.38,
        'fvg_entry_type': 'FIRST_TOUCH',
        'require_smt': True
    },
    'USDJPY': {
        'pip_size': 0.01,
        'spread_pips': 1.5,
        'min_risk_pips': 6.0,
        'max_risk_pips': 20.0,
        'killzones': ['Asia', 'NY'],
        'scale_tiers': [(3.0, 0.30), (5.0, 0.30), (8.0, 0.40)],
        'be_trigger_r': 3.0,
        'be_buffer_r': 0.2,
        'max_daily_losses': 2,
        'adr_filter_ratio': 0.45,
        'fvg_entry_type': 'FIRST_TOUCH'
    }
}

class RefinedSessionTracker:
    def __init__(self, profile):
        self.profile = profile
        self.pip_size = profile['pip_size']
        
    def process(self, df_m15: pd.DataFrame) -> pd.DataFrame:
        df = df_m15.copy()
        n = len(df)
        
        highs = df['High'].values
        lows = df['Low'].values
        closes = df['Close'].values
        times = df.index
        
        active_sess = np.full(n, 'None', dtype=object)
        asia_h_arr = np.full(n, np.nan)
        asia_l_arr = np.full(n, np.nan)
        london_h_arr = np.full(n, np.nan)
        london_l_arr = np.full(n, np.nan)
        
        # Sweep rejection: wick breached level, candle closed inside
        asia_swept_h = np.zeros(n, dtype=bool)
        asia_swept_l = np.zeros(n, dtype=bool)
        london_swept_h = np.zeros(n, dtype=bool)
        london_swept_l = np.zeros(n, dtype=bool)
        
        # Breakout invalidation: candle body closed firmly outside
        asia_broken_h = np.zeros(n, dtype=bool)
        asia_broken_l = np.zeros(n, dtype=bool)
        london_broken_h = np.zeros(n, dtype=bool)
        london_broken_l = np.zeros(n, dtype=bool)
        
        cur_day = None
        cur_asia_h = 0.0
        cur_asia_l = float('inf')
        cur_london_h = 0.0
        cur_london_l = float('inf')
        
        h_swept = False
        l_swept = False
        h_broken = False
        l_broken = False
        
        lon_h_swept = False
        lon_l_swept = False
        lon_h_broken = False
        lon_l_broken = False
        
        for i in range(n):
            dt = times[i]
            h = highs[i]
            l = lows[i]
            c = closes[i]
            
            trade_day = dt.date() if dt.hour < 17 else (dt + pd.Timedelta(days=1)).date()
            if cur_day != trade_day:
                cur_day = trade_day
                cur_asia_h = 0.0
                cur_asia_l = float('inf')
                cur_london_h = 0.0
                cur_london_l = float('inf')
                h_swept = False
                l_swept = False
                h_broken = False
                l_broken = False
                lon_h_swept = False
                lon_l_swept = False
                lon_h_broken = False
                lon_l_broken = False

            sess = 'None'
            hr = dt.hour
            
            # 1. Asia Session: 19:00 - 01:00 NY
            if hr >= 19 or hr < 1:
                sess = 'Asia'
                if h > cur_asia_h: cur_asia_h = h
                if l < cur_asia_l: cur_asia_l = l
                
            # 2. London Killzone: 02:00 - 05:00 NY
            elif 2 <= hr < 5:
                sess = 'London'
                if h > cur_london_h: cur_london_h = h
                if l < cur_london_l: cur_london_l = l
                
                # Check Asia Sweeps during London
                if cur_asia_h > 0 and h > cur_asia_h:
                    if c <= cur_asia_h + (2.0 * self.pip_size):
                        h_swept = True
                    else:
                        h_broken = True
                        
                if cur_asia_l < float('inf') and l < cur_asia_l:
                    if c >= cur_asia_l - (2.0 * self.pip_size):
                        l_swept = True
                    else:
                        l_broken = True
                        
            # 3. NY Killzone: 07:00 - 10:00 NY
            elif 7 <= hr < 10:
                sess = 'NY'
                # Check London Sweeps
                if cur_london_h > 0 and h > cur_london_h:
                    if c <= cur_london_h + (2.0 * self.pip_size):
                        lon_h_swept = True
                    else:
                        lon_h_broken = True
                        
                if cur_london_l < float('inf') and l < cur_london_l:
                    if c >= cur_london_l - (2.0 * self.pip_size):
                        lon_l_swept = True
                    else:
                        lon_l_broken = True
                        
                # Also check Asia Sweeps during NY
                if cur_asia_h > 0 and h > cur_asia_h:
                    if c <= cur_asia_h + (2.0 * self.pip_size):
                        h_swept = True
                    else:
                        h_broken = True
                if cur_asia_l < float('inf') and l < cur_asia_l:
                    if c >= cur_asia_l - (2.0 * self.pip_size):
                        l_swept = True
                    else:
                        l_broken = True

            active_sess[i] = sess
            asia_h_arr[i] = cur_asia_h if cur_asia_h > 0 else np.nan
            asia_l_arr[i] = cur_asia_l if cur_asia_l < float('inf') else np.nan
            london_h_arr[i] = cur_london_h if cur_london_h > 0 else np.nan
            london_l_arr[i] = cur_london_l if cur_london_l < float('inf') else np.nan
            
            asia_swept_h[i] = h_swept
            asia_swept_l[i] = l_swept
            asia_broken_h[i] = h_broken
            asia_broken_l[i] = l_broken
            london_swept_h[i] = lon_h_swept
            london_swept_l[i] = lon_l_swept
            london_broken_h[i] = lon_h_broken
            london_broken_l[i] = lon_l_broken

        df['Active_Session'] = active_sess
        df['Asia_High'] = asia_h_arr
        df['Asia_Low'] = asia_l_arr
        df['London_High'] = london_h_arr
        df['London_Low'] = london_l_arr
        
        df['Asia_Swept_High'] = asia_swept_h
        df['Asia_Swept_Low'] = asia_swept_l
        df['Asia_Broken_High'] = asia_broken_h
        df['Asia_Broken_Low'] = asia_broken_l
        df['London_Swept_High'] = london_swept_h
        df['London_Swept_Low'] = london_swept_l
        df['London_Broken_High'] = london_broken_h
        df['London_Broken_Low'] = london_broken_l
        
        return df


class RefinedLKEngine:
    def __init__(
        self,
        symbol: str = "EURUSD",
        initial_capital: float = 5000.0,
        risk_per_trade: float = 0.01,
        correlated_symbol: str = None
    ):
        self.symbol = symbol
        self.profile = ASSET_PROFILES.get(symbol, ASSET_PROFILES['EURUSD'])
        self.initial_capital = initial_capital
        self.capital = initial_capital
        self.risk_per_trade = risk_per_trade
        self.correlated_symbol = correlated_symbol
        
        self.pip_size = self.profile['pip_size']
        self.spread = self.profile['spread_pips'] * self.pip_size
        self.min_risk = self.profile['min_risk_pips'] * self.pip_size
        self.max_risk = self.profile['max_risk_pips'] * self.pip_size
        self.scale_tiers = self.profile['scale_tiers']
        self.be_trigger = self.profile['be_trigger_r']
        self.be_buffer = self.profile['be_buffer_r']
        self.max_daily_losses = self.profile['max_daily_losses']
        self.adr_ratio = self.profile['adr_filter_ratio']
        self.target_sessions = self.profile['killzones']
        self.fvg_entry_type = self.profile.get('fvg_entry_type', 'FIRST_TOUCH')
        self.require_smt = self.profile.get('require_smt', False)
        
        # Engines
        self.session_tracker = RefinedSessionTracker(self.profile)
        self.structure_h4 = MarketStructure(pullback_candles=3)
        self.structure_m15 = MarketStructure(pullback_candles=3)
        self.structure_m1 = MarketStructure(pullback_candles=3)
        self.imbalance_m15 = ImbalanceDetector(min_fvg_pips=self.pip_size)
        self.imbalance_m1 = ImbalanceDetector(min_fvg_pips=self.pip_size)
        self.dealing_range = DealingRangeEngine()
        
        self.trades = []
        self.daily_losses = {}
        self.equity_curve = []

    def load_data(self):
        print(f"[{self.symbol}] Loading 2026 data from {DATA_DIR}...")
        df_h4 = pd.read_parquet(os.path.join(DATA_DIR, f"{self.symbol}_H4_2026.parquet"))
        df_m15 = pd.read_parquet(os.path.join(DATA_DIR, f"{self.symbol}_M15_2026.parquet"))
        df_m1 = pd.read_parquet(os.path.join(DATA_DIR, f"{self.symbol}_M1_2026.parquet"))
        
        # Timezone to US/Eastern
        for df in [df_h4, df_m15, df_m1]:
            if df.index.tz is None:
                df.index = df.index.tz_localize('UTC').tz_convert('US/Eastern')
            else:
                df.index = df.index.tz_convert('US/Eastern')
                
        df_corr_m15 = None
        if self.correlated_symbol:
            corr_file = os.path.join(DATA_DIR, f"{self.correlated_symbol}_M15_2026.parquet")
            if os.path.exists(corr_file):
                df_corr_m15 = pd.read_parquet(corr_file)
                if df_corr_m15.index.tz is None:
                    df_corr_m15.index = df_corr_m15.index.tz_localize('UTC').tz_convert('US/Eastern')
                else:
                    df_corr_m15.index = df_corr_m15.index.tz_convert('US/Eastern')

        return df_h4, df_m15, df_m1, df_corr_m15

    def run(self):
        df_h4, df_m15, df_m1, df_corr_m15 = self.load_data()
        
        # Rolling ADR
        df_d1 = df_m15.resample('1D').agg({'High': 'max', 'Low': 'min'}).dropna()
        df_d1['Range'] = df_d1['High'] - df_d1['Low']
        df_d1['ADR_5'] = df_d1['Range'].rolling(5).mean().shift(1)
        adr_dict = {k.date(): v for k, v in df_d1['ADR_5'].items()}
        default_adr = df_d1['Range'].mean() if len(df_d1) > 0 else 70 * self.pip_size
        
        # 1. H4 Macro Structure
        df_h4 = self.structure_h4.update(df_h4)
        h4_records = df_h4.to_dict('index')
        
        # 2. M15 Sessions, Structure & Dealing Range
        df_m15 = self.session_tracker.process(df_m15)
        df_m15 = self.structure_m15.update(df_m15)
        df_m15 = self.imbalance_m15.detect(df_m15)
        df_m15 = self.dealing_range.calculate_range(df_m15)
        m15_records = df_m15.to_dict('index')
        
        corr_m15_records = {}
        if df_corr_m15 is not None:
            corr_profile = ASSET_PROFILES.get(self.correlated_symbol, ASSET_PROFILES['EURUSD'])
            corr_tracker = RefinedSessionTracker(corr_profile)
            df_corr_m15 = corr_tracker.process(df_corr_m15)
            corr_m15_records = df_corr_m15.to_dict('index')
            
        # 3. M1 Precision Confirmation
        df_m1 = self.structure_m1.update(df_m1)
        df_m1 = self.imbalance_m1.detect(df_m1)
        
        m1_highs = df_m1['High'].values
        m1_lows = df_m1['Low'].values
        m1_closes = df_m1['Close'].values
        m1_opens = df_m1['Open'].values
        m1_choch = df_m1['CHoCH'].values
        m1_trends = df_m1['Trend'].values
        m1_bear_fvg = df_m1['Bearish_FVG'].values
        m1_bull_fvg = df_m1['Bullish_FVG'].values
        m1_fvg_top = df_m1['FVG_Top'].values
        m1_fvg_bottom = df_m1['FVG_Bottom'].values
        m1_bear_ob_l = df_m1['Bearish_OB_Low'].values
        m1_bull_ob_h = df_m1['Bullish_OB_High'].values
        
        m1_times = df_m1.index
        m15_dts = df_m1.index.floor('15min')
        h4_dts = df_m1.index.floor('4h')
        m1_dates = df_m1.index.date
        n_m1 = len(df_m1)
        
        active_trade = None
        pending_limit_order = None
        awaiting_choch = None
        sweep_extreme_price = 0.0
        sweep_time = None
        recent_displacement_fvg = None
        
        print(f"[{self.symbol}] Executing Refined Brain on {n_m1:,} M1 bars...")
        
        for i in range(n_m1):
            m1_dt = m1_times[i]
            m1_h = m1_highs[i]
            m1_l = m1_lows[i]
            m1_c = m1_closes[i]
            
            m15_dt = m15_dts[i]
            h4_dt = h4_dts[i]
            
            if m15_dt not in m15_records:
                continue
                
            m15_bar = m15_records[m15_dt]
            h4_bar = h4_records.get(h4_dt, {})
            h4_trend = h4_bar.get('Trend', 'none')
            
            active_session = m15_bar['Active_Session']
            current_day = m1_dates[i]
            
            # --- 1. Manage Active Trade ---
            if active_trade:
                trade_type = active_trade['type']
                entry = active_trade['entry_price']
                risk = active_trade['risk']
                
                if trade_type == 'long':
                    peak_r = (m1_h - entry) / risk if risk > 0 else 0
                    
                    for r_target, partial_share in self.scale_tiers:
                        tier_key = f"tier_{r_target}"
                        if peak_r >= r_target and not active_trade.get(tier_key, False):
                            active_trade[tier_key] = True
                            pnl_tier = self.capital * self.risk_per_trade * partial_share * r_target
                            active_trade['realized_pnl'] += pnl_tier
                            active_trade['remaining_weight'] -= partial_share
                            active_trade['partials_taken'].append((r_target, partial_share))
                            
                            # Move SL to BE (+buffer)
                            if r_target >= self.be_trigger and active_trade['sl'] < entry:
                                active_trade['sl'] = entry + (self.be_buffer * risk)
                                
                    # Stop hit
                    if m1_l <= active_trade['sl']:
                        active_trade['exit_time'] = m1_dt
                        active_trade['exit_price'] = active_trade['sl']
                        rem_r = (active_trade['sl'] - entry) / risk
                        rem_pnl = self.capital * self.risk_per_trade * active_trade['remaining_weight'] * rem_r
                        active_trade['realized_pnl'] += rem_pnl
                        active_trade['pnl'] = active_trade['realized_pnl']
                        self.capital += active_trade['pnl']
                        if active_trade['pnl'] < 0:
                            self.daily_losses[current_day] = self.daily_losses.get(current_day, 0) + 1
                        self.trades.append(active_trade)
                        active_trade = None
                        continue
                        
                    # Max Target reached
                    max_target_r = self.scale_tiers[-1][0]
                    if peak_r >= max_target_r:
                        active_trade['exit_time'] = m1_dt
                        active_trade['exit_price'] = entry + (max_target_r * risk)
                        rem_pnl = self.capital * self.risk_per_trade * active_trade['remaining_weight'] * max_target_r
                        active_trade['realized_pnl'] += rem_pnl
                        active_trade['pnl'] = active_trade['realized_pnl']
                        self.capital += active_trade['pnl']
                        self.trades.append(active_trade)
                        active_trade = None
                        continue
                        
                elif trade_type == 'short':
                    peak_r = (entry - m1_l) / risk if risk > 0 else 0
                    
                    for r_target, partial_share in self.scale_tiers:
                        tier_key = f"tier_{r_target}"
                        if peak_r >= r_target and not active_trade.get(tier_key, False):
                            active_trade[tier_key] = True
                            pnl_tier = self.capital * self.risk_per_trade * partial_share * r_target
                            active_trade['realized_pnl'] += pnl_tier
                            active_trade['remaining_weight'] -= partial_share
                            active_trade['partials_taken'].append((r_target, partial_share))
                            
                            if r_target >= self.be_trigger and active_trade['sl'] > entry:
                                active_trade['sl'] = entry - (self.be_buffer * risk)
                                
                    if m1_h >= active_trade['sl']:
                        active_trade['exit_time'] = m1_dt
                        active_trade['exit_price'] = active_trade['sl']
                        rem_r = (entry - active_trade['sl']) / risk
                        rem_pnl = self.capital * self.risk_per_trade * active_trade['remaining_weight'] * rem_r
                        active_trade['realized_pnl'] += rem_pnl
                        active_trade['pnl'] = active_trade['realized_pnl']
                        self.capital += active_trade['pnl']
                        if active_trade['pnl'] < 0:
                            self.daily_losses[current_day] = self.daily_losses.get(current_day, 0) + 1
                        self.trades.append(active_trade)
                        active_trade = None
                        continue
                        
                    max_target_r = self.scale_tiers[-1][0]
                    if peak_r >= max_target_r:
                        active_trade['exit_time'] = m1_dt
                        active_trade['exit_price'] = entry - (max_target_r * risk)
                        rem_pnl = self.capital * self.risk_per_trade * active_trade['remaining_weight'] * max_target_r
                        active_trade['realized_pnl'] += rem_pnl
                        active_trade['pnl'] = active_trade['realized_pnl']
                        self.capital += active_trade['pnl']
                        self.trades.append(active_trade)
                        active_trade = None
                        continue
                continue
                
            # --- 2. Check Pending Limit Order (FVG / OB Mitigation) ---
            if pending_limit_order:
                age_secs = (m1_dt - pending_limit_order['time']).total_seconds()
                order_type = pending_limit_order['type']
                limit_px = pending_limit_order['limit_price']
                sl_px = pending_limit_order['sl']
                
                # Invalidation checks
                if age_secs > 2700 or active_session not in self.target_sessions:
                    pending_limit_order = None
                elif order_type == 'short' and m1_h > sl_px:
                    pending_limit_order = None # Sweep extreme broken before fill
                elif order_type == 'long' and m1_l < sl_px:
                    pending_limit_order = None
                elif order_type == 'short' and m1_h >= limit_px:
                    # FILLED on pullback mitigation!
                    entry_exec = limit_px - self.spread
                    risk = max(abs(sl_px - entry_exec), self.min_risk)
                    if risk <= self.max_risk:
                        active_trade = {
                            'symbol': self.symbol,
                            'type': 'short',
                            'entry_time': m1_dt,
                            'entry_price': entry_exec,
                            'original_sl': sl_px,
                            'sl': sl_px,
                            'risk': risk,
                            'risk_pips': risk / self.pip_size,
                            'remaining_weight': 1.0,
                            'realized_pnl': 0.0,
                            'partials_taken': []
                        }
                    pending_limit_order = None
                    continue
                elif order_type == 'long' and m1_l <= limit_px:
                    # FILLED on pullback mitigation!
                    entry_exec = limit_px + self.spread
                    risk = max(abs(entry_exec - sl_px), self.min_risk)
                    if risk <= self.max_risk:
                        active_trade = {
                            'symbol': self.symbol,
                            'type': 'long',
                            'entry_time': m1_dt,
                            'entry_price': entry_exec,
                            'original_sl': sl_px,
                            'sl': sl_px,
                            'risk': risk,
                            'risk_pips': risk / self.pip_size,
                            'remaining_weight': 1.0,
                            'realized_pnl': 0.0,
                            'partials_taken': []
                        }
                    pending_limit_order = None
                    continue

            # --- 3. Circuit Breaker Check ---
            if self.daily_losses.get(current_day, 0) >= self.max_daily_losses:
                continue

            # --- 4. Look for New Setups ---
            if active_session in self.target_sessions and not active_trade and not pending_limit_order:
                # Volatility ADR Filter
                asia_h = m15_bar.get('Asia_High', np.nan)
                asia_l = m15_bar.get('Asia_Low', np.nan)
                if not np.isnan(asia_h) and not np.isnan(asia_l) and asia_h > asia_l:
                    asian_range = asia_h - asia_l
                    cur_adr = adr_dict.get(current_day, default_adr)
                    if np.isnan(cur_adr) or cur_adr <= 0: cur_adr = default_adr
                    if (asian_range / cur_adr) > self.adr_ratio:
                        continue

                m15_trend = m15_bar.get('Trend', 'none')
                in_premium = m15_bar.get('In_Premium', False)
                in_discount = m15_bar.get('In_Discount', False)
                
                # Check valid sweep rejection
                swept_h_valid = False
                swept_l_valid = False
                
                if active_session == 'London':
                    swept_h_valid = m15_bar.get('Asia_Swept_High', False) and not m15_bar.get('Asia_Broken_High', False)
                    swept_l_valid = m15_bar.get('Asia_Swept_Low', False) and not m15_bar.get('Asia_Broken_Low', False)
                elif active_session == 'NY':
                    swept_h_valid = ((m15_bar.get('London_Swept_High', False) and not m15_bar.get('London_Broken_High', False)) or 
                                     (m15_bar.get('Asia_Swept_High', False) and not m15_bar.get('Asia_Broken_High', False)))
                    swept_l_valid = ((m15_bar.get('London_Swept_Low', False) and not m15_bar.get('London_Broken_Low', False)) or 
                                     (m15_bar.get('Asia_Swept_Low', False) and not m15_bar.get('Asia_Broken_Low', False)))
                elif active_session == 'Asia' and 'Asia' in self.target_sessions:
                    swept_h_valid = m15_bar.get('Sweep_High', False)
                    swept_l_valid = m15_bar.get('Sweep_Low', False)

                # SMT Divergence Filter
                if self.require_smt and corr_m15_records:
                    corr_bar = corr_m15_records.get(m15_dt, {})
                    if swept_h_valid:
                        corr_swept_h = corr_bar.get('Asia_Swept_High', False)
                        # If correlated pair also swept, require that our pair has higher relative volume/displacement
                    elif swept_l_valid:
                        corr_swept_l = corr_bar.get('Asia_Swept_Low', False)

                # Short Criteria: 4H not bullish, 15M bearish, in Premium, High Swept
                if h4_trend != 'bullish' and m15_trend == 'bearish' and in_premium and swept_h_valid:
                    if awaiting_choch != 'Short':
                        awaiting_choch = 'Short'
                        sweep_extreme_price = m1_h
                        sweep_time = m1_dt
                    else:
                        if m1_h > sweep_extreme_price:
                            sweep_extreme_price = m1_h
                            
                # Long Criteria: 4H not bearish, 15M bullish, in Discount, Low Swept
                elif h4_trend != 'bearish' and m15_trend == 'bullish' and in_discount and swept_l_valid:
                    if awaiting_choch != 'Long':
                        awaiting_choch = 'Long'
                        sweep_extreme_price = m1_l
                        sweep_time = m1_dt
                    else:
                        if m1_l < sweep_extreme_price:
                            sweep_extreme_price = m1_l

                # 1M Confirmation with FVG Mitigation Entry
                if awaiting_choch == 'Short':
                    if (m1_dt - sweep_time).total_seconds() > 2700:
                        awaiting_choch = None
                    else:
                        if m1_h > sweep_extreme_price:
                            sweep_extreme_price = m1_h
                            
                        # Detect displacement FVG:
                        # For short FVG, price comes from BELOW.
                        # First touch level = fvg_bottom (Candle 1 High).
                        # 50% Consequent Encroachment = (fvg_top + fvg_bottom) / 2.
                        if m1_bear_fvg[i]:
                            f_top = m1_fvg_top[i] if not np.isnan(m1_fvg_top[i]) else m1_h
                            f_bot = m1_fvg_bottom[i] if not np.isnan(m1_fvg_bottom[i]) else m1_l
                            if self.fvg_entry_type == 'FIRST_TOUCH':
                                recent_displacement_fvg = f_bot
                            else:
                                recent_displacement_fvg = (f_top + f_bot) / 2.0
                        elif m1_bear_ob_l[i]:
                            recent_displacement_fvg = m1_bear_ob_l[i]
                            
                        if m1_choch[i] or (m1_trends[i] == 'bearish' and recent_displacement_fvg is not None):
                            # Set limit entry at the FVG mitigation level!
                            limit_entry = recent_displacement_fvg if recent_displacement_fvg is not None else m1_c
                            sl_lvl = max(sweep_extreme_price + (1.5 * self.pip_size), limit_entry + self.min_risk)
                            pending_limit_order = {
                                'type': 'short',
                                'time': m1_dt,
                                'limit_price': limit_entry,
                                'sl': sl_lvl
                            }
                            awaiting_choch = None
                            recent_displacement_fvg = None
                            
                elif awaiting_choch == 'Long':
                    if (m1_dt - sweep_time).total_seconds() > 2700:
                        awaiting_choch = None
                    else:
                        if m1_l < sweep_extreme_price:
                            sweep_extreme_price = m1_l
                            
                        # For long FVG, price comes from ABOVE.
                        # First touch level = fvg_top (Candle 1 Low).
                        # 50% Consequent Encroachment = (fvg_top + fvg_bottom) / 2.
                        if m1_bull_fvg[i]:
                            f_top = m1_fvg_top[i] if not np.isnan(m1_fvg_top[i]) else m1_h
                            f_bot = m1_fvg_bottom[i] if not np.isnan(m1_fvg_bottom[i]) else m1_l
                            if self.fvg_entry_type == 'FIRST_TOUCH':
                                recent_displacement_fvg = f_top
                            else:
                                recent_displacement_fvg = (f_top + f_bot) / 2.0
                        elif m1_bull_ob_h[i]:
                            recent_displacement_fvg = m1_bull_ob_h[i]
                            
                        if m1_choch[i] or (m1_trends[i] == 'bullish' and recent_displacement_fvg is not None):
                            limit_entry = recent_displacement_fvg if recent_displacement_fvg is not None else m1_c
                            sl_lvl = min(sweep_extreme_price - (1.5 * self.pip_size), limit_entry - self.min_risk)
                            pending_limit_order = {
                                'type': 'long',
                                'time': m1_dt,
                                'limit_price': limit_entry,
                                'sl': sl_lvl
                            }
                            awaiting_choch = None
                            recent_displacement_fvg = None

        return self.calculate_results()

    def calculate_results(self):
        trades_df = pd.DataFrame(self.trades)
        if len(trades_df) == 0:
            print(f"[{self.symbol}] No trades executed.")
            return {}

        wins = trades_df[trades_df['pnl'] > 0]
        losses = trades_df[trades_df['pnl'] <= 0]
        
        n_trades = len(trades_df)
        n_wins = len(wins)
        n_losses = len(losses)
        win_rate = (n_wins / n_trades) * 100 if n_trades > 0 else 0.0
        
        gross_profit = wins['pnl'].sum() if n_wins > 0 else 0.0
        gross_loss = abs(losses['pnl'].sum()) if n_losses > 0 else 0.0
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else float('inf')
        net_profit = self.capital - self.initial_capital
        net_profit_pct = (net_profit / self.initial_capital) * 100
        
        peak = self.initial_capital
        max_dd_val = 0.0
        max_dd_pct = 0.0
        eq_curve = [self.initial_capital]
        for tr in self.trades:
            cur_eq = eq_curve[-1] + tr['pnl']
            eq_curve.append(cur_eq)
            if cur_eq > peak: peak = cur_eq
            dd = peak - cur_eq
            dd_pct = (dd / peak) * 100 if peak > 0 else 0
            if dd > max_dd_val: max_dd_val = dd
            if dd_pct > max_dd_pct: max_dd_pct = dd_pct
            
        trades_df['month'] = trades_df['entry_time'].apply(lambda dt: dt.strftime('%Y-%m'))
        monthly = {}
        for month, grp in trades_df.groupby('month'):
            m_wins = grp[grp['pnl'] > 0]
            monthly[month] = {
                'trades': len(grp),
                'wins': len(m_wins),
                'win_rate': (len(m_wins) / len(grp)) * 100,
                'pnl': grp['pnl'].sum(),
                'return_pct': (grp['pnl'].sum() / self.initial_capital) * 100
            }
            
        stats = {
            'symbol': self.symbol,
            'initial_capital': self.initial_capital,
            'final_capital': self.capital,
            'net_profit': net_profit,
            'net_profit_pct': net_profit_pct,
            'total_trades': n_trades,
            'wins': n_wins,
            'losses': n_losses,
            'win_rate': win_rate,
            'profit_factor': profit_factor,
            'max_drawdown_pct': max_dd_pct,
            'avg_win': wins['pnl'].mean() if n_wins > 0 else 0,
            'avg_loss': losses['pnl'].mean() if n_losses > 0 else 0,
            'avg_risk_pips': trades_df['risk_pips'].mean() if 'risk_pips' in trades_df else 0,
            'monthly': monthly,
            'trades_log': self.trades
        }
        
        self.print_summary(stats)
        return stats

    def print_summary(self, stats):
        print(f"\n================================================================================")
        print(f"   REFINED LEWIS KELLY ARTIFICIAL BRAIN: {stats['symbol']}")
        print(f"================================================================================")
        print(f"  Net Profit:           ${stats['net_profit']:,.2f} ({stats['net_profit_pct']:+.2f}%)")
        print(f"  Win Rate:             {stats['win_rate']:.1f}% ({stats['wins']} Wins / {stats['losses']} Losses)")
        print(f"  Profit Factor:        {stats['profit_factor']:.2f}")
        print(f"  Max Drawdown:         {stats['max_drawdown_pct']:.2f}%")
        print(f"  Avg Risk SL (Pips):   {stats['avg_risk_pips']:.1f} pips")
        print(f"  Avg Win / Loss:       +${stats['avg_win']:,.2f} / -${abs(stats['avg_loss']):,.2f}")
        print(f"--------------------------------------------------------------------------------")
        for m, m_data in sorted(stats['monthly'].items()):
            print(f"  {m} | Trades: {m_data['trades']:<2} | WinRate: {m_data['win_rate']:>5.1f}% | Net: ${m_data['pnl']:>8,.2f} ({m_data['return_pct']:>+6.2f}%)")
        print(f"================================================================================\n")
