import pandas as pd
import numpy as np
import os
import sys
import glob

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

try:
    from core.market_structure import MarketStructure
    from core.session_manager import SessionManager
    from core.imbalance import ImbalanceDetector
    from core.dealing_range import DealingRangeEngine
except ImportError:
    from lk_system.core.market_structure import MarketStructure
    from lk_system.core.session_manager import SessionManager
    from lk_system.core.imbalance import ImbalanceDetector
    from lk_system.core.dealing_range import DealingRangeEngine

class TickBacktestEngine:
    """
    Production-Grade Lewis Kelly Algorithmic Trading Engine.
    
    Faithfully implements Lewis Kelly's SMC rules:
    1. Multi-Timeframe Alignment: M15 Trend + 50% Dealing Range (Wholesale/Retail).
    2. Liquidity Raids: Asian High/Low or London High/Low sweeps during Killzones.
    3. Lower Timeframe Confirmation: 1M CHoCH (full body close) + 1M FVG/OB retest.
    4. Tight Invalidation Stop Loss: Placed 1-2 pips above/below 1M sweep extreme.
    5. Dynamic 6-Tier Scaling (+2.5R BE, +4.3R, +6R, +8.5R, +10R, +13R).
    6. Daily Circuit Breaker: Maximum 2 consecutive losses per trading day.
    """
    def __init__(self, execution_mode='MARKET_CONFIRMATION', target_sessions=None, pair_name='EURUSD', 
                 use_ml_filter=False, use_amcc=False, management_mode='LK_DYNAMIC', max_daily_losses=2):
        self.execution_mode = execution_mode
        self.target_sessions = target_sessions or ['London', 'NY']
        self.pair_name = pair_name
        self.use_ml_filter = use_ml_filter
        self.use_amcc = use_amcc
        self.management_mode = management_mode
        self.max_daily_losses = max_daily_losses
        
        self.capital = 5000.0
        self.initial_capital = 5000.0
        self.risk_per_trade = 0.01 # 1.0% equity risk
        
        # Lewis Kelly Scaling Tiers
        self.scale_tiers = [
            (2.5, 0.25),  # At +2.5R, close 25% and move SL to BE (+0.5R)
            (4.3, 0.15),  # At +4.3R, close 15% and trail SL behind 1M swings
            (6.0, 0.15),  # At +6.0R, close 15%
            (8.5, 0.15),  # At +8.5R, close 15%
            (10.0, 0.15), # At +10.0R, close 15%
            (13.0, 0.15), # At +13.0R, close final 15%
        ]
        
        self.trades = []
        self.daily_losses = {} # date -> count of losses
        
        # Engines
        self.structure_m15 = MarketStructure(pullback_candles=3)
        self.structure_m1 = MarketStructure(pullback_candles=3)
        self.session_manager = SessionManager()
        self.imbalance_m15 = ImbalanceDetector()
        self.imbalance_m1 = ImbalanceDetector()
        self.dealing_range = DealingRangeEngine()

    def load_and_resample(self, path_or_pattern):
        """
        Loads tick data from CSV or Parquet files and resamples to M1, M5, M15.
        """
        print(f"Loading tick data from {path_or_pattern}...")
        
        df_ticks = None
        if os.path.isdir(path_or_pattern):
            files = sorted(glob.glob(os.path.join(path_or_pattern, f"ticks_{self.pair_name}_*.parquet")))
            if not files:
                files = sorted(glob.glob(os.path.join(path_or_pattern, "*.parquet")))
            print(f"Reading {len(files)} parquet files...")
            dfs = []
            for f in files:
                dfs.append(pd.read_parquet(f))
            df_ticks = pd.concat(dfs, ignore_index=True)
            if 'time_dt' in df_ticks.columns:
                df_ticks['Datetime'] = pd.to_datetime(df_ticks['time_dt'])
            df_ticks.set_index('Datetime', inplace=True)
            df_ticks['Price'] = df_ticks['bid']
            
        elif path_or_pattern.endswith('.parquet'):
            df_ticks = pd.read_parquet(path_or_pattern)
            if 'time_dt' in df_ticks.columns:
                df_ticks['Datetime'] = pd.to_datetime(df_ticks['time_dt'])
            df_ticks.set_index('Datetime', inplace=True)
            df_ticks['Price'] = df_ticks['bid']
            
        elif path_or_pattern.endswith('.csv'):
            df_ticks = pd.read_csv(path_or_pattern, sep='\t' if '\t' in open(path_or_pattern).readline() else ',')
            if '<DATE>' in df_ticks.columns and '<TIME>' in df_ticks.columns:
                df_ticks['Datetime'] = pd.to_datetime(df_ticks['<DATE>'] + ' ' + df_ticks['<TIME>'])
            elif 'time' in df_ticks.columns:
                df_ticks['Datetime'] = pd.to_datetime(df_ticks['time'])
            df_ticks.set_index('Datetime', inplace=True)
            price_col = '<BID>' if '<BID>' in df_ticks.columns else ('bid' if 'bid' in df_ticks.columns else 'Price')
            df_ticks['Price'] = df_ticks[price_col]

        print("Resampling to M1, M5, and M15...")
        df_m1 = df_ticks['Price'].resample('1min').ohlc().dropna()
        df_m5 = df_ticks['Price'].resample('5min').ohlc().dropna()
        df_m15 = df_ticks['Price'].resample('15min').ohlc().dropna()
        
        df_m1.columns = ['Open', 'High', 'Low', 'Close']
        df_m5.columns = ['Open', 'High', 'Low', 'Close']
        df_m15.columns = ['Open', 'High', 'Low', 'Close']
        
        if df_m1.index.tz is None:
            df_m1.index = df_m1.index.tz_localize('UTC').tz_convert('US/Eastern')
            df_m5.index = df_m5.index.tz_localize('UTC').tz_convert('US/Eastern')
            df_m15.index = df_m15.index.tz_localize('UTC').tz_convert('US/Eastern')
        else:
            df_m1.index = df_m1.index.tz_convert('US/Eastern')
            df_m5.index = df_m5.index.tz_convert('US/Eastern')
            df_m15.index = df_m15.index.tz_convert('US/Eastern')
            
        return df_m1, df_m5, df_m15

    def run(self, df_m1, df_m5, df_m15):
        print(f"Running Lewis Kelly Artificial Trading Brain ({self.execution_mode})...")
        print(f"Total M15 bars: {len(df_m15)} | Total M1 bars: {len(df_m1)}")
        
        # 1. Prepare Higher Timeframe Context (M15)
        df_m15 = self.session_manager.update(df_m15)
        df_m15 = self.structure_m15.update(df_m15)
        df_m15 = self.imbalance_m15.detect(df_m15)
        df_m15 = self.dealing_range.calculate_range(df_m15)
        
        # 2. Prepare Lower Timeframe Execution (M1)
        df_m1 = self.structure_m1.update(df_m1)
        df_m1 = self.imbalance_m1.detect(df_m1)
        
        m15_records = df_m15.to_dict('index')
        
        active_trade = None
        pending_setup = None
        
        # Confirmation tracking state
        awaiting_choch = None # 'Short' or 'Long'
        sweep_extreme_price = 0.0
        sweep_time = None
        recent_displacement_fvg = None
        
        pip_size = 0.0001 if 'JPY' not in self.pair_name else 0.01
        
        # Fast array access for M1
        m1_highs = df_m1['High'].values
        m1_lows = df_m1['Low'].values
        m1_closes = df_m1['Close'].values
        m1_opens = df_m1['Open'].values
        m1_choch = df_m1['CHoCH'].values
        m1_trends = df_m1['Trend'].values
        m1_swing_high = df_m1['Swing_High'].values
        m1_swing_low = df_m1['Swing_Low'].values
        m1_bull_fvg = df_m1['Bullish_FVG'].values
        m1_bear_fvg = df_m1['Bearish_FVG'].values
        m1_bull_ob = df_m1['Bullish_OB'].values
        m1_bear_ob = df_m1['Bearish_OB'].values
        m1_bear_ob_l = df_m1['Bearish_OB_Low'].values
        m1_bull_ob_h = df_m1['Bullish_OB_High'].values
        m1_times = df_m1.index
        
        n_m1 = len(df_m1)
        
        for i in range(n_m1):
            m1_dt = m1_times[i]
            m1_h = m1_highs[i]
            m1_l = m1_lows[i]
            m1_c = m1_closes[i]
            
            m15_dt = m1_dt.floor('15min')
            if m15_dt not in m15_records:
                continue
                
            m15_bar = m15_records[m15_dt]
            active_session = m15_bar['Active_Session']
            current_day = m1_dt.date()
            
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
                            
                            # Move to Breakeven (+0.5R) at +2.5R
                            if r_target >= 2.5:
                                be_price = entry + (0.5 * risk)
                                if active_trade['sl'] < be_price:
                                    active_trade['sl'] = be_price
                                    
                    # Trail SL behind newly confirmed 1M swing lows after tier 4.3R
                    if active_trade.get('tier_4.3', False) and m1_swing_low[i]:
                        cand_sl = m1_l - (1.0 * pip_size)
                        if cand_sl > active_trade['sl']:
                            active_trade['sl'] = cand_sl
                            
                    # Check Stop Loss / Breakeven Hit
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
                        
                    # Check Max Target (+13R)
                    if peak_r >= 13.0:
                        active_trade['exit_time'] = m1_dt
                        active_trade['exit_price'] = entry + (13.0 * risk)
                        rem_pnl = self.capital * self.risk_per_trade * active_trade['remaining_weight'] * 13.0
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
                            
                            if r_target >= 2.5:
                                be_price = entry - (0.5 * risk)
                                if active_trade['sl'] > be_price:
                                    active_trade['sl'] = be_price
                                    
                    # Trail SL behind newly confirmed 1M swing highs after tier 4.3R
                    if active_trade.get('tier_4.3', False) and m1_swing_high[i]:
                        cand_sl = m1_h + (1.0 * pip_size)
                        if cand_sl < active_trade['sl']:
                            active_trade['sl'] = cand_sl
                            
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
                        
                    if peak_r >= 13.0:
                        active_trade['exit_time'] = m1_dt
                        active_trade['exit_price'] = entry - (13.0 * risk)
                        rem_pnl = self.capital * self.risk_per_trade * active_trade['remaining_weight'] * 13.0
                        active_trade['realized_pnl'] += rem_pnl
                        active_trade['pnl'] = active_trade['realized_pnl']
                        self.capital += active_trade['pnl']
                        self.trades.append(active_trade)
                        active_trade = None
                        continue
                continue
                
            # --- 2. Check Pending Setup Execution ---
            if pending_setup:
                # Cancel if pending setup is older than 20 minutes or session ended
                if (m1_dt - pending_setup['time']).total_seconds() > 1200 or active_session not in self.target_sessions:
                    pending_setup = None
                elif pending_setup['type'] == 'short':
                    # Entry triggered if price pulls back to entry level (or market execution mode)
                    if m1_h >= pending_setup['entry_price'] or self.execution_mode == 'MARKET_CONFIRMATION':
                        entry = pending_setup['entry_price'] if m1_h >= pending_setup['entry_price'] else m1_c
                        risk = abs(entry - pending_setup['sl'])
                        if 0.0003 <= risk <= 0.0025: # 3 to 25 pips valid risk range
                            active_trade = {
                                'type': 'short', 'entry_time': m1_dt, 'entry_price': entry,
                                'original_sl': pending_setup['sl'], 'sl': pending_setup['sl'],
                                'risk': risk, 'remaining_weight': 1.0, 'realized_pnl': 0.0,
                                'partials_taken': []
                            }
                            pending_setup = None
                elif pending_setup['type'] == 'long':
                    if m1_l <= pending_setup['entry_price'] or self.execution_mode == 'MARKET_CONFIRMATION':
                        entry = pending_setup['entry_price'] if m1_l <= pending_setup['entry_price'] else m1_c
                        risk = abs(entry - pending_setup['sl'])
                        if 0.0003 <= risk <= 0.0025:
                            active_trade = {
                                'type': 'long', 'entry_time': m1_dt, 'entry_price': entry,
                                'original_sl': pending_setup['sl'], 'sl': pending_setup['sl'],
                                'risk': risk, 'remaining_weight': 1.0, 'realized_pnl': 0.0,
                                'partials_taken': []
                            }
                            pending_setup = None
                            
            # --- 3. Look for New Confirmation Setups ---
            if self.daily_losses.get(current_day, 0) >= self.max_daily_losses:
                continue
                
            if active_session in self.target_sessions and not active_trade and not pending_setup:
                m15_trend = m15_bar['Trend']
                in_premium = m15_bar['In_Premium']
                in_discount = m15_bar['In_Discount']
                
                # Liquidity Sweeps
                swept_high = False
                swept_low = False
                
                if active_session == 'London':
                    swept_high = m15_bar.get('Asia_Ever_Swept_High', False) or m15_bar.get('Sweep_High', False)
                    swept_low = m15_bar.get('Asia_Ever_Swept_Low', False) or m15_bar.get('Sweep_Low', False)
                elif active_session == 'NY':
                    swept_high = m15_bar.get('London_Ever_Swept_High', False) or m15_bar.get('Asia_Ever_Swept_High', False) or m15_bar.get('Sweep_High', False)
                    swept_low = m15_bar.get('London_Ever_Swept_Low', False) or m15_bar.get('Asia_Ever_Swept_Low', False) or m15_bar.get('Sweep_Low', False)
                    
                # ─── TRIGGER 1: SHORT SWEEP DETECTED ───
                # Bearish M15 Trend + In Premium + High Swept
                if m15_trend == 'bearish' and in_premium and swept_high:
                    if awaiting_choch != 'Short':
                        awaiting_choch = 'Short'
                        sweep_extreme_price = m1_h
                        sweep_time = m1_dt
                    else:
                        if m1_h > sweep_extreme_price:
                            sweep_extreme_price = m1_h
                            
                # ─── TRIGGER 2: LONG SWEEP DETECTED ───
                # Bullish M15 Trend + In Discount + Low Swept
                elif m15_trend == 'bullish' and in_discount and swept_low:
                    if awaiting_choch != 'Long':
                        awaiting_choch = 'Long'
                        sweep_extreme_price = m1_l
                        sweep_time = m1_dt
                    else:
                        if m1_l < sweep_extreme_price:
                            sweep_extreme_price = m1_l

                # ─── CHECK 1M LOWER TIMEFRAME CONFIRMATION ───
                if awaiting_choch == 'Short':
                    # Allow up to 45 minutes for M1 reversal confirmation
                    if (m1_dt - sweep_time).total_seconds() > 2700:
                        awaiting_choch = None
                    else:
                        # Update sweep extreme if price made a higher high
                        if m1_h > sweep_extreme_price:
                            sweep_extreme_price = m1_h
                            
                        # Look for 1M Bearish FVG / OB created in displacement
                        if m1_bear_fvg[i]:
                            recent_displacement_fvg = m1_bear_ob_l[i] if not np.isnan(m1_bear_ob_l[i]) else m1_c
                            
                        # 1M CHoCH with Body Close!
                        if m1_choch[i] or (m1_trends[i] == 'bearish' and recent_displacement_fvg is not None):
                            entry_lvl = recent_displacement_fvg if recent_displacement_fvg is not None else m1_c
                            sl_lvl = sweep_extreme_price + (1.5 * pip_size)
                            pending_setup = {
                                'type': 'short',
                                'time': m1_dt,
                                'entry_price': entry_lvl,
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
                            
                        if m1_bull_fvg[i]:
                            recent_displacement_fvg = m1_bull_ob_h[i] if not np.isnan(m1_bull_ob_h[i]) else m1_c
                            
                        # 1M CHoCH with Body Close!
                        if m1_choch[i] or (m1_trends[i] == 'bullish' and recent_displacement_fvg is not None):
                            entry_lvl = recent_displacement_fvg if recent_displacement_fvg is not None else m1_c
                            sl_lvl = sweep_extreme_price - (1.5 * pip_size)
                            pending_setup = {
                                'type': 'long',
                                'time': m1_dt,
                                'entry_price': entry_lvl,
                                'sl': sl_lvl
                            }
                            awaiting_choch = None
                            recent_displacement_fvg = None

        self.stats = self._calculate_stats()
        self._print_report()
        return self.stats
        
    def _calculate_stats(self):
        total_trades = len(self.trades)
        if total_trades == 0:
            return {'Total Trades': 0, 'Win Rate': 0.0, 'Net Profit': 0.0, 'trades': []}
            
        wins = sum(1 for t in self.trades if t['pnl'] > 0)
        win_rate = (wins / total_trades) * 100
        total_pnl = sum(t['pnl'] for t in self.trades)
        profit_pct = (total_pnl / self.initial_capital) * 100
        
        return {'Total Trades': total_trades, 'Win Rate': win_rate, 'Net Profit': profit_pct, 'trades': self.trades}
        
    def _print_report(self):
        print(f"\n--- Lewis Kelly Artificial Trading Brain Report [{'-'.join(self.target_sessions)}] ---")
        total_trades = len(self.trades)
        if total_trades == 0:
            print("No trades executed.")
            return
            
        wins = sum(1 for t in self.trades if t['pnl'] > 0)
        win_rate = (wins / total_trades) * 100
        total_pnl = sum(t['pnl'] for t in self.trades)
        profit_pct = (total_pnl / self.initial_capital) * 100
        
        print(f"Total Trades: {total_trades}")
        print(f"Wins: {wins} | Losses: {total_trades - wins}")
        print(f"Win Rate: {win_rate:.2f}%")
        print(f"Starting Capital: ${self.initial_capital:.2f}")
        print(f"Ending Capital: ${self.capital:.2f}")
        print(f"Total Net Profit: ${total_pnl:.2f} ({profit_pct:.2f}%)")
        print("----------------------------------------------------------------------")
