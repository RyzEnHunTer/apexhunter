"""
Comprehensive Forex Basket Backtest: Fixed 1% Risk ($50 Flat per trade)
Starting Capital: $5,000.00
Period: 2026-01-01 to 2026-11-11 (10.5 Months)
Assets: EURUSD, GBPUSD, AUDUSD, USDJPY, EURJPY, USDCAD
"""

import sys
import os
import pandas as pd
import numpy as np
from datetime import datetime

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

try:
    from core.market_structure import MarketStructure
    from core.imbalance import ImbalanceDetector
    from core.dealing_range import DealingRangeEngine
    from backtest.run_full_jan_nov_backtest import LKArtificialBrainEngine
except ImportError:
    from lk_system.core.market_structure import MarketStructure
    from lk_system.core.imbalance import ImbalanceDetector
    from lk_system.core.dealing_range import DealingRangeEngine
    from lk_system.backtest.run_full_jan_nov_backtest import LKArtificialBrainEngine

DATA_DIR = os.path.join(PROJECT_ROOT, "data_jan_nov_2026")

# Pair Customization Configurations
PAIRS_CONFIG = {
    'EURUSD': {
        'tf_exec': 'M1',
        'pip_size': 0.0001,
        'spread_pips': 0.8,
        'min_risk_pips': 4.0,
        'max_risk_pips': 15.0,
        'target_sessions': ['London', 'NY'],
        'adr_ratio': 0.40
    },
    'GBPUSD': {
        'tf_exec': 'M5', # 5M execution filters out violent 1M wick noise
        'pip_size': 0.0001,
        'spread_pips': 1.2,
        'min_risk_pips': 6.0,
        'max_risk_pips': 20.0,
        'target_sessions': ['London', 'NY'],
        'adr_ratio': 0.40
    },
    'AUDUSD': {
        'tf_exec': 'M5', # 5M execution for clean swing expansions
        'pip_size': 0.0001,
        'spread_pips': 1.0,
        'min_risk_pips': 4.5,
        'max_risk_pips': 16.0,
        'target_sessions': ['London', 'NY'],
        'adr_ratio': 0.40
    },
    'USDJPY': {
        'tf_exec': 'M1',
        'pip_size': 0.01,
        'spread_pips': 1.5,
        'min_risk_pips': 7.0,
        'max_risk_pips': 25.0,
        'target_sessions': ['London', 'NY'],
        'adr_ratio': 0.45
    },
    'EURJPY': {
        'tf_exec': 'M5',
        'pip_size': 0.01,
        'spread_pips': 1.6,
        'min_risk_pips': 8.0,
        'max_risk_pips': 25.0,
        'target_sessions': ['London', 'NY'],
        'adr_ratio': 0.45
    },
    'USDCAD': {
        'tf_exec': 'M5',
        'pip_size': 0.0001,
        'spread_pips': 1.2,
        'min_risk_pips': 5.0,
        'max_risk_pips': 18.0,
        'target_sessions': ['NY'], # CAD active heavily in NY session
        'adr_ratio': 0.40
    }
}

class FixedRiskForexEngine(LKArtificialBrainEngine):
    def __init__(self, symbol, config, fixed_dollar_risk=50.0, tier1_rr=3.0):
        super().__init__(
            symbol=symbol,
            initial_capital=5000.0,
            risk_per_trade=0.01,
            spread_pips=config['spread_pips'],
            min_risk_pips=config['min_risk_pips'] * config['pip_size'],
            max_risk_pips=config['max_risk_pips'] * config['pip_size'],
            target_sessions=config['target_sessions'],
            max_daily_losses=2
        )
        self.config = config
        self.pip_size = config['pip_size']
        self.spread = config['spread_pips'] * self.pip_size
        self.fixed_risk = fixed_dollar_risk # Exactly $50 flat risk per trade
        self.tier1_rr = tier1_rr
        
        # Scaling Tiers:
        # tier1_rr (30% + Move SL to Breakeven +0.2R)
        # 5.0R (30%)
        # 8.0R (40% final target)
        self.scale_tiers = [(tier1_rr, 0.30), (5.0, 0.30), (8.0, 0.40)]
        
    def load_data(self):
        exec_tf = self.config['tf_exec']
        df_h4 = pd.read_parquet(os.path.join(DATA_DIR, f"{self.symbol}_H4_2026.parquet"))
        df_m15 = pd.read_parquet(os.path.join(DATA_DIR, f"{self.symbol}_M15_2026.parquet"))
        df_exec = pd.read_parquet(os.path.join(DATA_DIR, f"{self.symbol}_{exec_tf}_2026.parquet"))
        
        for df in [df_h4, df_m15, df_exec]:
            if df.index.tz is None:
                df.index = df.index.tz_localize('UTC').tz_convert('US/Eastern')
            else:
                df.index = df.index.tz_convert('US/Eastern')
                
        return df_h4, df_m15, df_exec

    def run_backtest(self):
        df_h4, df_m15, df_exec = self.load_data()
        
        df_d1 = df_m15.resample('1D').agg({'High': 'max', 'Low': 'min'}).dropna()
        df_d1['Range'] = df_d1['High'] - df_d1['Low']
        df_d1['ADR_5'] = df_d1['Range'].rolling(5).mean().shift(1)
        adr_dict = {k.date(): v for k, v in df_d1['ADR_5'].items()}
        default_adr = df_d1['Range'].mean() if len(df_d1) > 0 else 70 * self.pip_size
        
        # Structure & Sessions
        df_h4 = self.structure_h4.update(df_h4)
        df_m15 = self.session_manager.update(df_m15)
        df_m15 = self.structure_m15.update(df_m15)
        df_m15 = self.imbalance_m15.detect(df_m15)
        df_m15 = self.dealing_range.calculate_range(df_m15)
        
        df_exec = self.structure_m1.update(df_exec)
        df_exec = self.imbalance_m1.detect(df_exec)
        
        h4_records = df_h4.to_dict('index')
        m15_records = df_m15.to_dict('index')
        
        exec_highs = df_exec['High'].values
        exec_lows = df_exec['Low'].values
        exec_closes = df_exec['Close'].values
        exec_choch = df_exec['CHoCH'].values
        exec_trends = df_exec['Trend'].values
        exec_bear_fvg = df_exec['Bearish_FVG'].values
        exec_bull_fvg = df_exec['Bullish_FVG'].values
        exec_bear_ob_l = df_exec['Bearish_OB_Low'].values
        exec_bull_ob_h = df_exec['Bullish_OB_High'].values
        
        exec_times = df_exec.index
        m15_dts = df_exec.index.floor('15min')
        h4_dts = df_exec.index.floor('4h')
        exec_dates = df_exec.index.date
        n_bars = len(df_exec)
        
        active_trade = None
        pending_setup = None
        awaiting_choch = None
        sweep_extreme_price = 0.0
        sweep_time = None
        recent_displacement_fvg = None
        
        for i in range(n_bars):
            dt = exec_times[i]
            h = exec_highs[i]
            l = exec_lows[i]
            c = exec_closes[i]
            
            m15_dt = m15_dts[i]
            h4_dt = h4_dts[i]
            
            if m15_dt not in m15_records:
                continue
                
            m15_bar = m15_records[m15_dt]
            h4_bar = h4_records.get(h4_dt, {})
            h4_trend = h4_bar.get('Trend', 'none')
            
            active_session = m15_bar['Active_Session']
            current_day = exec_dates[i]
            
            # 1. Manage Active Trade
            if active_trade:
                trade_type = active_trade['type']
                entry = active_trade['entry_price']
                risk = active_trade['risk']
                
                if trade_type == 'long':
                    peak_r = (h - entry) / risk if risk > 0 else 0
                    
                    for r_target, partial_share in self.scale_tiers:
                        tier_key = f"tier_{r_target}"
                        if peak_r >= r_target and not active_trade.get(tier_key, False):
                            active_trade[tier_key] = True
                            # Fixed Dollar Risk Formula
                            pnl_tier = self.fixed_risk * partial_share * r_target
                            active_trade['realized_pnl'] += pnl_tier
                            active_trade['remaining_weight'] -= partial_share
                            active_trade['partials_taken'].append((r_target, partial_share))
                            
                            if r_target >= self.tier1_rr and active_trade['sl'] < entry:
                                active_trade['sl'] = entry + (0.2 * risk)
                                active_trade['be_time'] = dt
                                
                    if l <= active_trade['sl']:
                        active_trade['exit_time'] = dt
                        active_trade['exit_price'] = active_trade['sl']
                        rem_r = (active_trade['sl'] - entry) / risk
                        rem_pnl = self.fixed_risk * active_trade['remaining_weight'] * rem_r
                        active_trade['realized_pnl'] += rem_pnl
                        active_trade['pnl'] = active_trade['realized_pnl']
                        self.capital += active_trade['pnl']
                        if active_trade['pnl'] < 0:
                            self.daily_losses[current_day] = self.daily_losses.get(current_day, 0) + 1
                        self.trades.append(active_trade)
                        active_trade = None
                        continue
                        
                    if peak_r >= 8.0:
                        active_trade['exit_time'] = dt
                        active_trade['exit_price'] = entry + (8.0 * risk)
                        rem_pnl = self.fixed_risk * active_trade['remaining_weight'] * 8.0
                        active_trade['realized_pnl'] += rem_pnl
                        active_trade['pnl'] = active_trade['realized_pnl']
                        self.capital += active_trade['pnl']
                        self.trades.append(active_trade)
                        active_trade = None
                        continue
                        
                elif trade_type == 'short':
                    peak_r = (entry - l) / risk if risk > 0 else 0
                    
                    for r_target, partial_share in self.scale_tiers:
                        tier_key = f"tier_{r_target}"
                        if peak_r >= r_target and not active_trade.get(tier_key, False):
                            active_trade[tier_key] = True
                            pnl_tier = self.fixed_risk * partial_share * r_target
                            active_trade['realized_pnl'] += pnl_tier
                            active_trade['remaining_weight'] -= partial_share
                            active_trade['partials_taken'].append((r_target, partial_share))
                            
                            if r_target >= self.tier1_rr and active_trade['sl'] > entry:
                                active_trade['sl'] = entry - (0.2 * risk)
                                active_trade['be_time'] = dt
                                
                    if h >= active_trade['sl']:
                        active_trade['exit_time'] = dt
                        active_trade['exit_price'] = active_trade['sl']
                        rem_r = (entry - active_trade['sl']) / risk
                        rem_pnl = self.fixed_risk * active_trade['remaining_weight'] * rem_r
                        active_trade['realized_pnl'] += rem_pnl
                        active_trade['pnl'] = active_trade['realized_pnl']
                        self.capital += active_trade['pnl']
                        if active_trade['pnl'] < 0:
                            self.daily_losses[current_day] = self.daily_losses.get(current_day, 0) + 1
                        self.trades.append(active_trade)
                        active_trade = None
                        continue
                        
                    if peak_r >= 8.0:
                        active_trade['exit_time'] = dt
                        active_trade['exit_price'] = entry - (8.0 * risk)
                        rem_pnl = self.fixed_risk * active_trade['remaining_weight'] * 8.0
                        active_trade['realized_pnl'] += rem_pnl
                        active_trade['pnl'] = active_trade['realized_pnl']
                        self.capital += active_trade['pnl']
                        self.trades.append(active_trade)
                        active_trade = None
                        continue
                continue
                
            # 2. Check Pending Setup
            if pending_setup:
                if (dt - pending_setup['time']).total_seconds() > 2700 or active_session not in self.target_sessions:
                    pending_setup = None
                elif pending_setup['type'] == 'short':
                    entry = pending_setup['entry_price']
                    entry_exec = entry - self.spread
                    risk = max(abs(pending_setup['sl'] - entry_exec), self.min_risk)
                    if risk <= self.max_risk:
                        active_trade = {
                            'symbol': self.symbol,
                            'type': 'short',
                            'entry_time': dt,
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
                            'entry_time': dt,
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

            # 3. Circuit Breaker
            if self.daily_losses.get(current_day, 0) >= self.max_daily_losses:
                continue

            # 4. Look for New Setups
            if active_session in self.target_sessions and not active_trade and not pending_setup:
                asia_h = m15_bar.get('Asia_High', np.nan)
                asia_l = m15_bar.get('Asia_Low', np.nan)
                if not np.isnan(asia_h) and not np.isnan(asia_l) and asia_h > asia_l:
                    asian_range = asia_h - asia_l
                    cur_adr = adr_dict.get(current_day, default_adr)
                    if np.isnan(cur_adr) or cur_adr <= 0: cur_adr = default_adr
                    if (asian_range / cur_adr) > self.config['adr_ratio']:
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

                # Short Setup Criteria
                if h4_trend != 'bullish' and m15_trend == 'bearish' and in_premium and swept_high:
                    if awaiting_choch != 'Short':
                        awaiting_choch = 'Short'
                        sweep_extreme_price = h
                        sweep_time = dt
                    else:
                        if h > sweep_extreme_price:
                            sweep_extreme_price = h
                            
                # Long Setup Criteria
                elif h4_trend != 'bearish' and m15_trend == 'bullish' and in_discount and swept_low:
                    if awaiting_choch != 'Long':
                        awaiting_choch = 'Long'
                        sweep_extreme_price = l
                        sweep_time = dt
                    else:
                        if l < sweep_extreme_price:
                            sweep_extreme_price = l

                # Lower Timeframe Confirmation
                if awaiting_choch == 'Short':
                    if (dt - sweep_time).total_seconds() > 2700:
                        awaiting_choch = None
                    else:
                        if h > sweep_extreme_price:
                            sweep_extreme_price = h
                        if exec_bear_fvg[i]:
                            recent_displacement_fvg = exec_bear_ob_l[i] if not np.isnan(exec_bear_ob_l[i]) else c
                        if exec_choch[i] or (exec_trends[i] == 'bearish' and recent_displacement_fvg is not None):
                            entry_lvl = recent_displacement_fvg if recent_displacement_fvg is not None else c
                            sl_lvl = max(sweep_extreme_price + (2.0 * self.pip_size), entry_lvl + self.min_risk)
                            pending_setup = {
                                'type': 'short',
                                'time': dt,
                                'entry_price': entry_lvl,
                                'sl': sl_lvl
                            }
                            awaiting_choch = None
                            recent_displacement_fvg = None
                            
                elif awaiting_choch == 'Long':
                    if (dt - sweep_time).total_seconds() > 2700:
                        awaiting_choch = None
                    else:
                        if l < sweep_extreme_price:
                            sweep_extreme_price = l
                        if exec_bull_fvg[i]:
                            recent_displacement_fvg = exec_bull_ob_h[i] if not np.isnan(exec_bull_ob_h[i]) else c
                        if exec_choch[i] or (exec_trends[i] == 'bullish' and recent_displacement_fvg is not None):
                            entry_lvl = recent_displacement_fvg if recent_displacement_fvg is not None else c
                            sl_lvl = min(sweep_extreme_price - (2.0 * self.pip_size), entry_lvl - self.min_risk)
                            pending_setup = {
                                'type': 'long',
                                'time': dt,
                                'entry_price': entry_lvl,
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
                'losses': len(grp) - len(m_wins),
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
            'max_drawdown_dollar': max_dd_val,
            'max_drawdown_pct': max_dd_pct,
            'avg_win': wins['pnl'].mean() if n_wins > 0 else 0.0,
            'avg_loss': losses['pnl'].mean() if n_losses > 0 else 0.0,
            'avg_risk_pips': trades_df['risk_pips'].mean() if 'risk_pips' in trades_df else 0.0,
            'monthly': monthly,
            'trades_log': self.trades
        }
        
        self.print_summary(stats)
        return stats

    def print_summary(self, stats):
        print(f"\n================================================================================")
        print(f"  ASSET: {stats['symbol']} (Execution: {self.config['tf_exec']}) | Fixed 1% Risk ($50 flat)")
        print(f"================================================================================")
        print(f"  Net Profit:           ${stats['net_profit']:,.2f} ({stats['net_profit_pct']:+.2f}%)")
        print(f"  Trades:               {stats['total_trades']} ({stats['wins']} Wins / {stats['losses']} Losses)")
        print(f"  Win Rate:             {stats['win_rate']:.1f}%")
        print(f"  Profit Factor:        {stats['profit_factor']:.2f}")
        print(f"  Max Drawdown:         ${stats['max_drawdown_dollar']:,.2f} ({stats['max_drawdown_pct']:.2f}%)")
        print(f"  Avg Risk SL (Pips):   {stats['avg_risk_pips']:.1f} pips")
        print(f"  Avg Win / Loss:       +${stats['avg_win']:,.2f} / -${abs(stats['avg_loss']):,.2f}")
        print(f"--------------------------------------------------------------------------------")
        for m, m_data in sorted(stats['monthly'].items()):
            print(f"  {m} | Trades: {m_data['trades']:<2} | WinRate: {m_data['win_rate']:>5.1f}% | Net: ${m_data['pnl']:>8,.2f} ({m_data['return_pct']:>+6.2f}%)")
        print(f"================================================================================\n")


def run_all_forex_pairs():
    all_results = {}
    total_trades = 0
    total_wins = 0
    total_losses = 0
    total_profit = 0.0
    
    print("\n" + "#" * 80)
    print("      RUNNING COMPLETE 2026 OUT-OF-SAMPLE FOREX BASKET (FIXED 1% / $50 RISK)")
    print("#" * 80)
    
    for pair, cfg in PAIRS_CONFIG.items():
        engine = FixedRiskForexEngine(symbol=pair, config=cfg, fixed_dollar_risk=50.0)
        res = engine.run_backtest()
        all_results[pair] = res
        if res:
            total_trades += res['total_trades']
            total_wins += res['wins']
            total_losses += res['losses']
            total_profit += res['net_profit']
            
    print("\n" + "=" * 80)
    print("      CONSOLIDATED 2026 FOREX PORTFOLIO PERFORMANCE SUMMARY")
    print("=" * 80)
    print(f"  Starting Balance:      $5,000.00")
    print(f"  Total Trades:          {total_trades}")
    print(f"  Portfolio Wins:        {total_wins} ({total_wins/total_trades*100:.1f}%)")
    print(f"  Portfolio Losses:      {total_losses} ({total_losses/total_trades*100:.1f}%)")
    print(f"  Total Net Profit:      ${total_profit:,.2f} (+{total_profit/5000*100:.2f}%)")
    print("=" * 80)

if __name__ == '__main__':
    run_all_forex_pairs()

