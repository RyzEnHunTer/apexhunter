import os
import sys
import pickle
import pandas as pd
import numpy as np

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
if PROJECT_ROOT not in sys.path: sys.path.insert(0, PROJECT_ROOT)
if PROJECT_ROOT not in sys.path: sys.path.insert(0, PROJECT_ROOT)

from lk_system.core.market_regime_handler import (
    DynamicStrategyMarketSelector,
    CircuitBreakerGuardian
)

with open('backtest/all_raw_candidates.pkl', 'rb') as f:
    all_candidates = pickle.load(f)

print(f"Loaded {len(all_candidates)} raw candidate setups from pickled parquet generation.")

def run_sim(candidates, use_market_analyser: bool):
    starting_capital = 5000.0
    guardian = CircuitBreakerGuardian(
        base_capital=starting_capital,
        emergency_daily_cb_pct=0.045,
        emergency_max_cb_pct=0.080,
        news_soft_cap_pct=0.030,
        normal_daily_cap_pct=0.040,
        risk_per_trade_pct=0.010,
        max_concurrent_trades=3
    )
    selector = DynamicStrategyMarketSelector(guardian) if use_market_analyser else None

    balance = starting_capital
    peak_balance = starting_capital
    max_equity_dd_dollars = 0.0
    max_equity_dd_pct = 0.0

    current_day = None
    day_start_balance = starting_capital
    max_daily_dd_dollars = 0.0
    max_daily_dd_pct = 0.0

    active_positions = []
    executed_trades = []
    blocked_trades = []
    switches = []
    last_strategy = None

    for trade in candidates:
        t_entry = trade['entry_time']
        t_day = t_entry.date()

        if current_day != t_day:
            if current_day is not None:
                day_drop = max(0.0, day_start_balance - balance)
                day_drop_pct = (day_drop / day_start_balance) * 100.0
                if day_drop > max_daily_dd_dollars:
                    max_daily_dd_dollars = day_drop
                    max_daily_dd_pct = day_drop_pct
            current_day = t_day
            day_start_balance = balance

        active_positions = [p for p in active_positions if p['exit_time'] > t_entry]
        risk_bearing = [p for p in active_positions if p.get('be_time') is None or p['be_time'] > t_entry]
        guardian.active_open_positions = len(risk_bearing)

        if any(p['symbol'] == trade['symbol'] for p in active_positions):
            blocked_trades.append({'trade': trade, 'reason': 'SYMBOL_ALREADY_ACTIVE'})
            continue

        if len(risk_bearing) >= 3:
            blocked_trades.append({'trade': trade, 'reason': 'MAX_SLOTS_REACHED'})
            continue

        if use_market_analyser:
            strat_key = 'APEXHUNTER_1_2_1' if 'APEX' in trade['strategy'] else 'LK_FOREX_SMC'
            approved, reason, risk_usd = selector.evaluate_market_and_route(
                strategy_candidate=strat_key,
                symbol=trade['symbol'],
                current_time=t_entry,
                current_balance=balance,
                current_equity=balance,
                candidate_params=trade['params']
            )
            if not approved:
                blocked_trades.append({'trade': trade, 'reason': reason})
                continue
        else:
            guardian.reset_daily_if_new_utc_day(t_entry, balance)
            can_open, cb_reason = guardian.can_open_new_trade(balance, balance, False)
            if not can_open:
                blocked_trades.append({'trade': trade, 'reason': f'CB_HALT: {cb_reason}'})
                continue

        if last_strategy != trade['strategy']:
            switches.append({'time': t_entry, 'from': last_strategy, 'to': trade['strategy']})
            last_strategy = trade['strategy']

        guardian.record_trade_opened()
        pnl = trade['pnl']
        balance += pnl
        if balance > peak_balance: peak_balance = balance
        eq_dd = peak_balance - balance
        eq_dd_pct = (eq_dd / peak_balance) * 100.0
        if eq_dd > max_equity_dd_dollars:
            max_equity_dd_dollars = eq_dd
            max_equity_dd_pct = eq_dd_pct

        guardian.record_trade_closed(pnl)
        trade_exec = dict(trade)
        trade_exec['balance_after'] = round(balance, 2)
        active_positions.append(trade_exec)
        executed_trades.append(trade_exec)

    df_exec = pd.DataFrame(executed_trades)
    wins = (df_exec['pnl'] > 0).sum() if len(df_exec) > 0 else 0
    loss = (df_exec['pnl'] < 0).sum() if len(df_exec) > 0 else 0
    wr = (wins / len(df_exec) * 100.0) if len(df_exec) > 0 else 0.0
    net_pnl = df_exec['pnl'].sum() if len(df_exec) > 0 else 0.0
    gw = df_exec[df_exec['pnl'] > 0]['pnl'].sum() if len(df_exec) > 0 else 0.0
    gl = abs(df_exec[df_exec['pnl'] < 0]['pnl'].sum()) if len(df_exec) > 0 else 0.0
    pf = (gw / gl) if gl > 0 else np.nan

    apex_df = df_exec[df_exec['strategy'] == 'APEXHUNTER_1_2_1']
    lk_df = df_exec[df_exec['strategy'] == 'LEWIS_KELLY_SMC']

    return {
        'total_executed': len(df_exec),
        'total_blocked': len(blocked_trades),
        'wins': wins,
        'losses': loss,
        'win_rate': wr,
        'gross_profit': gw,
        'gross_loss': gl,
        'net_profit': net_pnl,
        'profit_factor': pf,
        'final_balance': balance,
        'max_dd_dollars': max_equity_dd_dollars,
        'max_dd_pct': max_equity_dd_pct,
        'max_daily_dd_dollars': max_daily_dd_dollars,
        'max_daily_dd_pct': max_daily_dd_pct,
        'switches_count': len(switches),
        'apex_trades': len(apex_df),
        'apex_pnl': apex_df['pnl'].sum() if len(apex_df) > 0 else 0.0,
        'apex_wr': (apex_df['pnl'] > 0).mean() * 100.0 if len(apex_df) > 0 else 0.0,
        'apex_pf': (apex_df[apex_df['pnl'] > 0]['pnl'].sum() / abs(apex_df[apex_df['pnl'] < 0]['pnl'].sum())) if (apex_df['pnl'] < 0).sum() > 0 else np.nan,
        'lk_trades': len(lk_df),
        'lk_pnl': lk_df['pnl'].sum() if len(lk_df) > 0 else 0.0,
        'lk_wr': (lk_df['pnl'] > 0).mean() * 100.0 if len(lk_df) > 0 else 0.0,
        'lk_pf': (lk_df[lk_df['pnl'] > 0]['pnl'].sum() / abs(lk_df[lk_df['pnl'] < 0]['pnl'].sum())) if (lk_df['pnl'] < 0).sum() > 0 else np.nan,
        'df_exec': df_exec,
        'blocked_trades': blocked_trades
    }

res_with = run_sim(all_candidates, use_market_analyser=True)
res_without = run_sim(all_candidates, use_market_analyser=False)

res_with['df_exec'].to_csv('backtest/with_market_analyser_trades.csv', index=False)
res_without['df_exec'].to_csv('backtest/without_market_analyser_executed_trades.csv', index=False)

print("\n" + "=" * 115)
print("      HEAD-TO-HEAD AUDIT: WITH MARKET ANALYSER vs WITHOUT MARKET ANALYSER (SIMULTANEOUS)")
print("      (100% Raw Parquet Bar Replay | Exact Same Raw Candidate Pool | Starting Capital: $5,000)")
print("=" * 115)
print(f"{'Performance Metric':<35} | {'WITH Market Analyser':<36} | {'WITHOUT Market Analyser':<36}")
print("-" * 115)
print(f"{'Total Signals Evaluated':<35} | {len(all_candidates):<36} | {len(all_candidates):<36}")
print(f"{'Executed Trades':<35} | {res_with['total_executed']} trades ({res_with['total_executed']/len(all_candidates)*100:.1f}%) | {res_without['total_executed']} trades ({res_without['total_executed']/len(all_candidates)*100:.1f}%)")
print(f"{'Blocked Signals':<35} | {res_with['total_blocked']} (Regime + Slot Caps) | {res_without['total_blocked']} (Slots Only)")
print(f"{'Portfolio Win Rate':<35} | {res_with['win_rate']:.2f}% ({res_with['wins']}W / {res_with['losses']}L) | {res_without['win_rate']:.2f}% ({res_without['wins']}W / {res_without['losses']}L)")
print(f"{'Total Net Profit':<35} | +${res_with['net_profit']:,.2f} (+{res_with['net_profit']/50:.1f}%) | +${res_without['net_profit']:,.2f} (+{res_without['net_profit']/50:.1f}%)")
print(f"{'Profit Factor (PF)':<35} | {res_with['profit_factor']:.2f} | {res_without['profit_factor']:.2f}")
print(f"{'Ending Account Balance':<35} | ${res_with['final_balance']:,.2f} | ${res_without['final_balance']:,.2f}")
print(f"{'Max Peak-to-Trough DD':<35} | ${res_with['max_dd_dollars']:,.2f} ({res_with['max_dd_pct']:.2f}%) | ${res_without['max_dd_dollars']:,.2f} ({res_without['max_dd_pct']:.2f}%)")
print(f"{'Worst Single-Day Drop':<35} | ${res_with['max_daily_dd_dollars']:,.2f} ({res_with['max_daily_dd_pct']:.2f}%) | ${res_without['max_daily_dd_dollars']:,.2f} ({res_without['max_daily_dd_pct']:.2f}%)")
print(f"{'Strategy Switches Executed':<35} | {res_with['switches_count']} active switches | {res_without['switches_count']} interleaved switches")
print("-" * 115)
print("STRATEGY BREAKDOWN COMPARISON:")
print(f"  * ApexHunter (Gold & Nasdaq)    | +${res_with['apex_pnl']:,.2f} ({res_with['apex_trades']} trds, {res_with['apex_wr']:.1f}% WR, {res_with['apex_pf']:.2f} PF) | +${res_without['apex_pnl']:,.2f} ({res_without['apex_trades']} trds, {res_without['apex_wr']:.1f}% WR, {res_without['apex_pf']:.2f} PF)")
print(f"  * Lewis Kelly SMC (Forex)       | +${res_with['lk_pnl']:,.2f} ({res_with['lk_trades']} trds, {res_with['lk_wr']:.1f}% WR, {res_with['lk_pf']:.2f} PF) | +${res_without['lk_pnl']:,.2f} ({res_without['lk_trades']} trds, {res_without['lk_wr']:.1f}% WR, {res_without['lk_pf']:.2f} PF)")
print("=" * 115)
