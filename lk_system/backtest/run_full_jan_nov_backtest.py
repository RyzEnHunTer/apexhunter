"""
Full Jan-Nov 2026 Out-Of-Sample Backtest Engine
Replicating Lewis Kelly's Complete SMC Trading Brain
Period: 2026-01-01 to 2026-11-11
Pairs: EURUSD & GBPUSD
"""

import sys
import os
import glob
import json
import pandas as pd
import numpy as np
from datetime import datetime

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

DATA_DIR = os.path.join(PROJECT_ROOT, "data_jan_nov_2026")

class LKArtificialBrainEngine:
    def __init__(
        self,
        symbol: str = "EURUSD",
        initial_capital: float = 5000.0,
        risk_per_trade: float = 0.01,
        spread_pips: float = 1.0,
        min_risk_pips: float = 0.0006,
        max_risk_pips: float = 0.0025,
        target_sessions: list = None,
        max_daily_losses: int = 2
    ):
        self.symbol = symbol
        self.capital = initial_capital
        self.initial_capital = initial_capital
        self.risk_per_trade = risk_per_trade
        self.pip_size = 0.0001
        self.spread = spread_pips * self.pip_size
        self.min_risk = min_risk_pips
        self.max_risk = max_risk_pips
        self.target_sessions = target_sessions or ['London', 'NY']
        self.max_daily_losses = max_daily_losses
        
        # Lewis Kelly Scaling Tiers
        # Tier 1 (+3.0R): Take 30% partial, move SL to breakeven (+0.2R)
        # Tier 2 (+5.0R): Take 30% partial
        # Tier 3 (+8.0R): Take remaining 40% (Target Hit)
        self.scale_tiers = [
            (3.0, 0.30),
            (5.0, 0.30),
            (8.0, 0.40)
        ]
        
        # Sub-engines
        self.structure_h4 = MarketStructure(pullback_candles=3)
        self.structure_m15 = MarketStructure(pullback_candles=3)
        self.structure_m1 = MarketStructure(pullback_candles=3)
        self.session_manager = SessionManager()
        self.imbalance_m15 = ImbalanceDetector()
        self.imbalance_m1 = ImbalanceDetector()
        self.dealing_range = DealingRangeEngine()
        
        # State tracking
        self.trades = []
        self.daily_losses = {}
        self.equity_curve = []
        
    def load_data(self):
        print(f"[{self.symbol}] Loading pre-saved 2026 Parquet files...")
        df_h4 = pd.read_parquet(os.path.join(DATA_DIR, f"{self.symbol}_H4_2026.parquet"))
        df_m15 = pd.read_parquet(os.path.join(DATA_DIR, f"{self.symbol}_M15_2026.parquet"))
        df_m1 = pd.read_parquet(os.path.join(DATA_DIR, f"{self.symbol}_M1_2026.parquet"))
        
        # Convert all timestamps from UTC to US/Eastern (New York) for accurate ICT Killzones
        if df_h4.index.tz is None:
            df_h4.index = df_h4.index.tz_localize('UTC').tz_convert('US/Eastern')
        else:
            df_h4.index = df_h4.index.tz_convert('US/Eastern')
            
        if df_m15.index.tz is None:
            df_m15.index = df_m15.index.tz_localize('UTC').tz_convert('US/Eastern')
        else:
            df_m15.index = df_m15.index.tz_convert('US/Eastern')
            
        if df_m1.index.tz is None:
            df_m1.index = df_m1.index.tz_localize('UTC').tz_convert('US/Eastern')
        else:
            df_m1.index = df_m1.index.tz_convert('US/Eastern')
            
        print(f"[{self.symbol}] Loaded & aligned: H4={len(df_h4):,}, M15={len(df_m15):,}, M1={len(df_m1):,} bars.")
        return df_h4, df_m15, df_m1

    def run_backtest(self):
        df_h4, df_m15, df_m1 = self.load_data()
        
        print(f"[{self.symbol}] Calculating Rolling 5-Day ADR...")
        # Compute Daily Bars from M15 to get dynamic 5-day ADR
        df_d1 = df_m15.resample('1D').agg({'High': 'max', 'Low': 'min'}).dropna()
        df_d1['Range'] = df_d1['High'] - df_d1['Low']
        df_d1['ADR_5'] = df_d1['Range'].rolling(5).mean().shift(1)
        adr_dict = {k.date(): v for k, v in df_d1['ADR_5'].items()}
        default_adr = 0.0075 if 'EUR' in self.symbol else 0.0100
        
        print(f"[{self.symbol}] Processing Market Structure & Session Context...")
        
        # 1. H4 Macro Structure
        df_h4 = self.structure_h4.update(df_h4)
        
        # 2. M15 Session & Dealing Range
        df_m15 = self.session_manager.update(df_m15)
        df_m15 = self.structure_m15.update(df_m15)
        df_m15 = self.imbalance_m15.detect(df_m15)
        df_m15 = self.dealing_range.calculate_range(df_m15)
        
        # 3. M1 Precision Confirmation
        df_m1 = self.structure_m1.update(df_m1)
        df_m1 = self.imbalance_m1.detect(df_m1)
        
        h4_records = df_h4.to_dict('index')
        m15_records = df_m15.to_dict('index')
        
        # Pre-extract M1 NumPy arrays for ultra-fast loop execution
        m1_highs = df_m1['High'].values
        m1_lows = df_m1['Low'].values
        m1_closes = df_m1['Close'].values
        m1_choch = df_m1['CHoCH'].values
        m1_trends = df_m1['Trend'].values
        m1_bear_fvg = df_m1['Bearish_FVG'].values
        m1_bull_fvg = df_m1['Bullish_FVG'].values
        m1_bear_ob_l = df_m1['Bearish_OB_Low'].values
        m1_bull_ob_h = df_m1['Bullish_OB_High'].values
        m1_times = df_m1.index
        m15_dts = df_m1.index.floor('15min')
        h4_dts = df_m1.index.floor('4h')
        m1_dates = df_m1.index.date
        n_m1 = len(df_m1)
        
        active_trade = None
        pending_setup = None
        awaiting_choch = None
        sweep_extreme_price = 0.0
        sweep_time = None
        recent_displacement_fvg = None
        
        print(f"[{self.symbol}] Executing Lewis Kelly Artificial Brain on {n_m1:,} M1 bars...")
        
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
                    
                    # Scaling Tiers
                    for r_target, partial_share in self.scale_tiers:
                        tier_key = f"tier_{r_target}"
                        if peak_r >= r_target and not active_trade.get(tier_key, False):
                            active_trade[tier_key] = True
                            pnl_tier = self.capital * self.risk_per_trade * partial_share * r_target
                            active_trade['realized_pnl'] += pnl_tier
                            active_trade['remaining_weight'] -= partial_share
                            active_trade['partials_taken'].append((r_target, partial_share))
                            
                            # Move to Breakeven (+0.2R) at Tier 1 (3.0R)
                            if r_target >= 3.0 and active_trade['sl'] < entry:
                                active_trade['sl'] = entry + (0.2 * risk)
                                
                    # Check Stop Loss Hit
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
                        self.equity_curve.append({'time': m1_dt, 'equity': self.capital})
                        self.trades.append(active_trade)
                        active_trade = None
                        continue
                        
                    # Check Full Target Hit (Tier 3 = 8.0R)
                    if peak_r >= 8.0:
                        active_trade['exit_time'] = m1_dt
                        active_trade['exit_price'] = entry + (8.0 * risk)
                        rem_pnl = self.capital * self.risk_per_trade * active_trade['remaining_weight'] * 8.0
                        active_trade['realized_pnl'] += rem_pnl
                        active_trade['pnl'] = active_trade['realized_pnl']
                        self.capital += active_trade['pnl']
                        self.equity_curve.append({'time': m1_dt, 'equity': self.capital})
                        self.trades.append(active_trade)
                        active_trade = None
                        continue
                        
                elif trade_type == 'short':
                    peak_r = (entry - m1_l) / risk if risk > 0 else 0
                    
                    # Scaling Tiers
                    for r_target, partial_share in self.scale_tiers:
                        tier_key = f"tier_{r_target}"
                        if peak_r >= r_target and not active_trade.get(tier_key, False):
                            active_trade[tier_key] = True
                            pnl_tier = self.capital * self.risk_per_trade * partial_share * r_target
                            active_trade['realized_pnl'] += pnl_tier
                            active_trade['remaining_weight'] -= partial_share
                            active_trade['partials_taken'].append((r_target, partial_share))
                            
                            if r_target >= 3.0 and active_trade['sl'] > entry:
                                active_trade['sl'] = entry - (0.2 * risk)
                                
                    # Check Stop Loss Hit
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
                        self.equity_curve.append({'time': m1_dt, 'equity': self.capital})
                        self.trades.append(active_trade)
                        active_trade = None
                        continue
                        
                    # Check Full Target Hit (Tier 3 = 8.0R)
                    if peak_r >= 8.0:
                        active_trade['exit_time'] = m1_dt
                        active_trade['exit_price'] = entry - (8.0 * risk)
                        rem_pnl = self.capital * self.risk_per_trade * active_trade['remaining_weight'] * 8.0
                        active_trade['realized_pnl'] += rem_pnl
                        active_trade['pnl'] = active_trade['realized_pnl']
                        self.capital += active_trade['pnl']
                        self.equity_curve.append({'time': m1_dt, 'equity': self.capital})
                        self.trades.append(active_trade)
                        active_trade = None
                        continue
                continue
                
            # --- 2. Check Pending Setup Execution ---
            if pending_setup:
                # Cancel setup if older than 45 minutes or outside session
                if (m1_dt - pending_setup['time']).total_seconds() > 2700 or active_session not in self.target_sessions:
                    pending_setup = None
                elif pending_setup['type'] == 'short':
                    entry = pending_setup['entry_price']
                    entry_exec = entry - self.spread
                    risk = max(abs(pending_setup['sl'] - entry_exec), self.min_risk)
                    if risk <= self.max_risk:
                        active_trade = {
                            'symbol': self.symbol,
                            'type': 'short',
                            'entry_time': m1_dt,
                            'entry_price': entry_exec,
                            'original_sl': pending_setup['sl'],
                            'sl': pending_setup['sl'],
                            'risk': risk,
                            'risk_pips': risk / self.pip_size,
                            'remaining_weight': 1.0,
                            'realized_pnl': 0.0,
                            'partials_taken': []
                        }
                    pending_setup = None
                    continue
                    
                elif pending_setup['type'] == 'long':
                    entry = pending_setup['entry_price']
                    entry_exec = entry + self.spread
                    risk = max(abs(entry_exec - pending_setup['sl']), self.min_risk)
                    if risk <= self.max_risk:
                        active_trade = {
                            'symbol': self.symbol,
                            'type': 'long',
                            'entry_time': m1_dt,
                            'entry_price': entry_exec,
                            'original_sl': pending_setup['sl'],
                            'sl': pending_setup['sl'],
                            'risk': risk,
                            'risk_pips': risk / self.pip_size,
                            'remaining_weight': 1.0,
                            'realized_pnl': 0.0,
                            'partials_taken': []
                        }
                    pending_setup = None
                    continue

            # --- 3. Circuit Breaker Check ---
            if self.daily_losses.get(current_day, 0) >= self.max_daily_losses:
                continue

            # --- 4. Look for New Setups ---
            if active_session in self.target_sessions and not active_trade and not pending_setup:
                # Check Asian Range / ADR Volatility Filter
                asia_h = m15_bar.get('Asia_High', np.nan)
                asia_l = m15_bar.get('Asia_Low', np.nan)
                if not np.isnan(asia_h) and not np.isnan(asia_l) and asia_h > asia_l:
                    asian_range = asia_h - asia_l
                    cur_adr = adr_dict.get(current_day, default_adr)
                    if np.isnan(cur_adr) or cur_adr <= 0:
                        cur_adr = default_adr
                    if (asian_range / cur_adr) > 0.40:
                        # Asian range is too wide / high compression chop -> skip day
                        continue
                    
                m15_trend = m15_bar.get('Trend', 'none')
                in_premium = m15_bar.get('In_Premium', False)
                in_discount = m15_bar.get('In_Discount', False)
                
                swept_high = False
                swept_low = False
                
                if active_session == 'London':
                    swept_high = m15_bar.get('Asia_Ever_Swept_High', False) or m15_bar.get('Sweep_High', False)
                    swept_low = m15_bar.get('Asia_Ever_Swept_Low', False) or m15_bar.get('Sweep_Low', False)
                elif active_session == 'NY':
                    swept_high = (m15_bar.get('London_Ever_Swept_High', False) or 
                                  m15_bar.get('Asia_Ever_Swept_High', False) or 
                                  m15_bar.get('Sweep_High', False))
                    swept_low = (m15_bar.get('London_Ever_Swept_Low', False) or 
                                 m15_bar.get('Asia_Ever_Swept_Low', False) or 
                                 m15_bar.get('Sweep_Low', False))

                # Macro Trend & 50% Dealing Range Alignment
                # Short Setup Criteria:
                if h4_trend != 'bullish' and m15_trend == 'bearish' and in_premium and swept_high:
                    if awaiting_choch != 'Short':
                        awaiting_choch = 'Short'
                        sweep_extreme_price = m1_h
                        sweep_time = m1_dt
                    else:
                        if m1_h > sweep_extreme_price:
                            sweep_extreme_price = m1_h
                            
                # Long Setup Criteria:
                elif h4_trend != 'bearish' and m15_trend == 'bullish' and in_discount and swept_low:
                    if awaiting_choch != 'Long':
                        awaiting_choch = 'Long'
                        sweep_extreme_price = m1_l
                        sweep_time = m1_dt
                    else:
                        if m1_l < sweep_extreme_price:
                            sweep_extreme_price = m1_l

                # Lower Timeframe 1M Confirmation
                if awaiting_choch == 'Short':
                    # Invalidation: if sweep happened > 45 mins ago without CHoCH, cancel
                    if (m1_dt - sweep_time).total_seconds() > 2700:
                        awaiting_choch = None
                    else:
                        if m1_h > sweep_extreme_price:
                            sweep_extreme_price = m1_h
                        if m1_bear_fvg[i]:
                            recent_displacement_fvg = m1_bear_ob_l[i] if not np.isnan(m1_bear_ob_l[i]) else m1_c
                        if m1_choch[i] or (m1_trends[i] == 'bearish' and recent_displacement_fvg is not None):
                            entry_lvl = recent_displacement_fvg if recent_displacement_fvg is not None else m1_c
                            sl_lvl = max(sweep_extreme_price + (2.0 * self.pip_size), entry_lvl + self.min_risk)
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
                        if m1_choch[i] or (m1_trends[i] == 'bullish' and recent_displacement_fvg is not None):
                            entry_lvl = recent_displacement_fvg if recent_displacement_fvg is not None else m1_c
                            sl_lvl = min(sweep_extreme_price - (2.0 * self.pip_size), entry_lvl - self.min_risk)
                            pending_setup = {
                                'type': 'long',
                                'time': m1_dt,
                                'entry_price': entry_lvl,
                                'sl': sl_lvl
                            }
                            awaiting_choch = None
                            recent_displacement_fvg = None

        return self.calculate_results()

    def calculate_results(self):
        trades_df = pd.DataFrame(self.trades)
        if len(trades_df) == 0:
            print(f"[{self.symbol}] No trades executed during the backtest period.")
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
        
        # Max Drawdown Calculation
        peak = self.initial_capital
        max_dd_val = 0.0
        max_dd_pct = 0.0
        eq_curve = [self.initial_capital]
        
        for tr in self.trades:
            cur_eq = eq_curve[-1] + tr['pnl']
            eq_curve.append(cur_eq)
            if cur_eq > peak:
                peak = cur_eq
            dd = peak - cur_eq
            dd_pct = (dd / peak) * 100 if peak > 0 else 0
            if dd > max_dd_val:
                max_dd_val = dd
            if dd_pct > max_dd_pct:
                max_dd_pct = dd_pct
                
        # Monthly Breakdown
        trades_df['month'] = trades_df['entry_time'].apply(lambda dt: dt.strftime('%Y-%m'))
        monthly = {}
        for month, grp in trades_df.groupby('month'):
            m_wins = grp[grp['pnl'] > 0]
            m_losses = grp[grp['pnl'] <= 0]
            monthly[month] = {
                'trades': len(grp),
                'wins': len(m_wins),
                'losses': len(m_losses),
                'win_rate': (len(m_wins) / len(grp)) * 100,
                'pnl': grp['pnl'].sum(),
                'return_pct': (grp['pnl'].sum() / self.initial_capital) * 100
            }
            
        avg_win = wins['pnl'].mean() if n_wins > 0 else 0.0
        avg_loss = losses['pnl'].mean() if n_losses > 0 else 0.0
        avg_risk_pips = trades_df['risk_pips'].mean() if 'risk_pips' in trades_df else 0.0

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
            'max_drawdown_dollar': max_dd_val,
            'max_drawdown_pct': max_dd_pct,
            'avg_win': avg_win,
            'avg_loss': avg_loss,
            'avg_risk_pips': avg_risk_pips,
            'monthly': monthly,
            'trades_log': self.trades
        }
        
        self.print_summary(stats)
        return stats

    def print_summary(self, stats):
        print(f"\n" + "=" * 80)
        print(f"    LEWIS KELLY ARTIFICIAL TRADING BRAIN: JAN-NOV 2026 VERIFICATION")
        print(f"    Pair: {stats['symbol']} | Initial Capital: ${stats['initial_capital']:,.2f}")
        print(f"=" * 80)
        print(f"  Final Capital:        ${stats['final_capital']:,.2f}")
        print(f"  Net Profit:           ${stats['net_profit']:,.2f} ({stats['net_profit_pct']:+.2f}%)")
        print(f"  Total Trades:         {stats['total_trades']}")
        print(f"  Win Rate:             {stats['win_rate']:.1f}% ({stats['wins']} Wins / {stats['losses']} Losses)")
        print(f"  Profit Factor:        {stats['profit_factor']:.2f}")
        print(f"  Max Drawdown:         ${stats['max_drawdown_dollar']:,.2f} ({stats['max_drawdown_pct']:.2f}%)")
        print(f"  Average Risk (Pips):  {stats['avg_risk_pips']:.1f} pips")
        print(f"  Average Win / Loss:   +${stats['avg_win']:,.2f} / -${abs(stats['avg_loss']):,.2f}")
        print(f"-" * 80)
        print(f"  MONTH-BY-MONTH PERFORMANCE BREAKDOWN:")
        print(f"  {'Month':<10} | {'Trades':<8} | {'Win Rate':<10} | {'Net P&L ($)':<14} | {'Monthly Return':<12}")
        print(f"  {'-'*10}-|-{'-'*8}-|-{'-'*10}-|-{'-'*14}-|-{'-'*14}")
        for m, m_data in sorted(stats['monthly'].items()):
            print(f"  {m:<10} | {m_data['trades']:<8} | {m_data['win_rate']:>6.1f}%    | ${m_data['pnl']:>10,.2f}   | {m_data['return_pct']:>+8.2f}%")
        print(f"=" * 80)


def run_full_verification():
    results = {}
    for sym, spread, min_risk in [('EURUSD', 1.0, 0.0006), ('GBPUSD', 1.2, 0.0007)]:
        print(f"\n>>> STARTING JAN-NOV 2026 BACKTEST FOR {sym} <<<")
        engine = LKArtificialBrainEngine(
            symbol=sym,
            initial_capital=5000.0,
            risk_per_trade=0.01,
            spread_pips=spread,
            min_risk_pips=min_risk,
            max_risk_pips=0.0030
        )
        res = engine.run_backtest()
        results[sym] = res

    # Save comprehensive results to JSON
    clean_results = {}
    for sym, res in results.items():
        if not res:
            continue
        clean_res = dict(res)
        if 'trades_log' in clean_res:
            clean_trades = []
            for tr in clean_res['trades_log']:
                tr_copy = dict(tr)
                for k in ['entry_time', 'exit_time']:
                    if k in tr_copy and isinstance(tr_copy[k], (pd.Timestamp, datetime)):
                        tr_copy[k] = tr_copy[k].isoformat()
                clean_trades.append(tr_copy)
            clean_res['trades_log'] = clean_trades
        clean_results[sym] = clean_res

    out_file = os.path.join(PROJECT_ROOT, "backtest", "jan_nov_2026_results.json")
    with open(out_file, 'w') as f:
        json.dump(clean_results, f, indent=2)
    print(f"\nAll verification results successfully exported to {out_file}!")

if __name__ == '__main__':
    run_full_verification()
