import os
import sys
import time
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Any, Optional
import pandas as pd
import numpy as np

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
DC_ROOT = os.path.join(os.path.dirname(PROJECT_ROOT), "DC")
if PROJECT_ROOT not in sys.path: sys.path.insert(0, PROJECT_ROOT)
if PROJECT_ROOT not in sys.path: sys.path.insert(0, PROJECT_ROOT)
if DC_ROOT not in sys.path: sys.path.insert(0, DC_ROOT)
if os.path.join(DC_ROOT, 'v1.2') not in sys.path: sys.path.insert(0, os.path.join(DC_ROOT, 'v1.2'))

from lk_system.backtest.test_forex_basket_fixed_risk import FixedRiskForexEngine, PAIRS_CONFIG
from live_bot import InstitutionalDCCBot
from lk_system.core.market_regime_handler import (
    DynamicStrategyMarketSelector,
    CircuitBreakerGuardian
)

def generate_all_raw_candidates() -> List[Dict[str, Any]]:
    print("\n[Step 1/2] Generating Lewis Kelly SMC trades on 6 Forex pairs...")
    t0 = time.time()
    lk_raw_trades = []
    for pair, cfg in PAIRS_CONFIG.items():
        engine = FixedRiskForexEngine(symbol=pair, config=cfg, fixed_dollar_risk=50.0)
        engine.run_backtest()
        for t in engine.trades:
            entry_t = pd.to_datetime(t['entry_time'], utc=True)
            exit_t = pd.to_datetime(t['exit_time'], utc=True) if t.get('exit_time') else entry_t + timedelta(hours=4)
            be_t = None
            if t.get('tier_3.0', False) or t.get('pnl', 0) > 0:
                be_t = entry_t + (exit_t - entry_t) * 0.35

            lk_raw_trades.append({
                'strategy': 'LEWIS_KELLY_SMC',
                'symbol': pair,
                'direction': t['type'].upper(),
                'entry_time': entry_t,
                'exit_time': exit_t,
                'be_time': be_t,
                'pnl': round(t['pnl'], 2),
                'risk_dollars': 50.0,
                'params': {
                    'sl_pips': float(t.get('sl_pips', 8.5)),
                    'asian_range_pct_adr': float(t.get('asian_range_pct_adr', 0.25)),
                    'is_high_impact_news_window': False
                }
            })
    print(f"  Lewis Kelly: {len(lk_raw_trades)} trades in {round(time.time() - t0, 1)}s.")

    print("\n[Step 2/2] Generating ApexHunter v1.2.1 Model B trades on Gold & Nasdaq...")
    t1 = time.time()
    apex_specs = {
        'XAUUSD': {'pip': 0.10, 'contract': 100.0, 'comm_lot': 5.0, 'sl_mult': 1.30, 'tp1_rr': 1.40, 'tp2_rr': 2.10},
        'NAS100': {'pip': 1.0,  'contract': 10.0,  'comm_lot': 5.0, 'sl_mult': 1.40, 'tp1_rr': 1.40, 'tp2_rr': 2.10}
    }
    apex_raw_trades = []
    dc_cache = os.path.join(os.path.dirname(PROJECT_ROOT), "DC", "data_cache")
    if not os.path.exists(dc_cache):
        dc_cache = os.path.join(PROJECT_ROOT, "tick_cache")

    for sym, spec in apex_specs.items():
        m5_p1 = os.path.join(dc_cache, f"m5_bars_{sym}.parquet")
        h1_p1 = os.path.join(dc_cache, f"h1_bars_{sym}.parquet")
        if not os.path.exists(m5_p1) or not os.path.exists(h1_p1):
            continue
        df_m5 = pd.read_parquet(m5_p1)
        df_h1 = pd.read_parquet(h1_p1)
        if 'time_utc' in df_m5.columns: df_m5 = df_m5.set_index('time_utc')
        if 'time_utc' in df_h1.columns: df_h1 = df_h1.set_index('time_utc')
        if 'tick_volume' in df_m5.columns: df_m5 = df_m5.rename(columns={'tick_volume': 'volume'})

        df_prep = InstitutionalDCCBot.prepare_dataframe(df_m5, df_h1, sym)
        val_pt = spec['contract']

        active_trade = None
        daily_losses = 0
        current_day = ""

        for i in range(25, len(df_prep)):
            c_bar = df_prep.iloc[i]
            t = df_prep.index[i]
            t_hour = t.hour
            t_day = t.strftime('%Y-%m-%d')

            if t_day != current_day:
                current_day = t_day
                daily_losses = 0

            high_p = float(c_bar['high'])
            low_p = float(c_bar['low'])
            close_p = float(c_bar['close'])

            if active_trade is not None:
                pos = active_trade
                is_buy = (pos['direction'] == "BUY")
                sl_hit = (low_p <= pos['current_sl']) if is_buy else (high_p >= pos['current_sl'])
                tp1_hit = (high_p >= pos['tp1_price']) if is_buy else (low_p <= pos['tp1_price'])
                tp2_hit = (high_p >= pos['tp2_price']) if is_buy else (low_p <= pos['tp2_price'])

                if tp2_hit:
                    pos['exit_time'] = t
                    p1 = pos['part_lots'] * pos['tp1_dist'] * val_pt
                    p2 = pos['run_lots'] * pos['tp2_dist'] * val_pt
                    comm = pos['total_lots'] * spec['comm_lot']
                    pos['pnl'] = round(p1 + p2 - comm, 2)
                    apex_raw_trades.append(pos)
                    active_trade = None
                    continue
                elif sl_hit:
                    pos['exit_time'] = t
                    comm = pos['total_lots'] * spec['comm_lot']
                    if pos['tp1_hit']:
                        p1 = pos['part_lots'] * pos['tp1_dist'] * val_pt
                        pos['pnl'] = round(p1 - comm, 2)
                    else:
                        pos['pnl'] = round(-50.0 - comm, 2)
                        daily_losses += 1
                    apex_raw_trades.append(pos)
                    active_trade = None
                    continue
                elif tp1_hit and not pos['tp1_hit']:
                    pos['tp1_hit'] = True
                    pos['be_time'] = t
                    pos['current_sl'] = pos['entry_price']

            if active_trade is None:
                if daily_losses >= 2: continue
                if not (6 <= t_hour < 19): continue

                bias = int(c_bar['bias_1h']) if not pd.isna(c_bar['bias_1h']) else 0
                p_diff = float(c_bar['p_diff'])
                c_diff = float(c_bar['c_diff'])
                vwap = float(c_bar['vwap_5m'])
                h1_e20 = float(c_bar['ema20_1h'])

                b_flip = (bias == 1) and (p_diff <= 0) and (c_diff > 0) and (close_p > vwap) and (close_p > h1_e20)
                s_flip = (bias == -1) and (p_diff >= 0) and (c_diff < 0) and (close_p < vwap) and (close_p < h1_e20)

                if b_flip or s_flip:
                    has_sw, _, _ = InstitutionalDCCBot.detect_liquidity_sweep(df_prep, i, 1 if b_flip else -1, 20, 8)
                    if not has_sw: continue

                    direction = "BUY" if b_flip else "SELL"
                    atr = float(c_bar['atr_1h'])
                    sl_dist = spec['sl_mult'] * atr
                    tp1_dist = spec['tp1_rr'] * sl_dist
                    tp2_dist = spec['tp2_rr'] * sl_dist

                    entry_p = close_p
                    sl_p = entry_p - sl_dist if direction == "BUY" else entry_p + sl_dist
                    tp1_p = entry_p + tp1_dist if direction == "BUY" else entry_p - tp1_dist
                    tp2_p = entry_p + tp2_dist if direction == "BUY" else entry_p - tp2_dist

                    raw_lots = 50.0 / (sl_dist * val_pt)
                    tot_lots = max(0.01, round(raw_lots, 2))
                    part_lots = round(tot_lots * 0.50, 2)
                    run_lots = round(tot_lots - part_lots, 2)

                    active_trade = {
                        'strategy': 'APEXHUNTER_1_2_1',
                        'symbol': sym,
                        'direction': direction,
                        'entry_time': t,
                        'entry_price': entry_p,
                        'current_sl': sl_p,
                        'tp1_price': tp1_p,
                        'tp2_price': tp2_p,
                        'sl_dist': sl_dist,
                        'tp1_dist': tp1_dist,
                        'tp2_dist': tp2_dist,
                        'total_lots': tot_lots,
                        'part_lots': part_lots,
                        'run_lots': run_lots,
                        'tp1_hit': False,
                        'be_time': None,
                        'risk_dollars': 50.0,
                        'params': {
                            'stretch_ratio': float(c_bar['stretch_ratio']) if not pd.isna(c_bar['stretch_ratio']) else 0.5,
                            'daily_ci': float(c_bar['daily_ci']) if not pd.isna(c_bar['daily_ci']) else 45.0,
                            'vol_ratio': float(c_bar['vol_ratio']) if not pd.isna(c_bar['vol_ratio']) else 1.0,
                            'adx_1h': float(c_bar['adx_1h']) if not pd.isna(c_bar['adx_1h']) else 25.0,
                            'is_high_impact_news_window': False
                        }
                    }

    print(f"  ApexHunter: {len(apex_raw_trades)} trades in {round(time.time() - t1, 1)}s.")
    return sorted(lk_raw_trades + apex_raw_trades, key=lambda x: x['entry_time'])

def simulate_portfolio(all_candidates: List[Dict[str, Any]], use_market_analyser: bool) -> Dict[str, Any]:
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

    for trade in all_candidates:
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

        # Expire closed positions
        active_positions = [p for p in active_positions if p['exit_time'] > t_entry]

        # Count risk bearing positions (Dynamic Breakeven Slot Liberation)
        risk_bearing = [p for p in active_positions if p.get('be_time') is None or p['be_time'] > t_entry]
        guardian.active_open_positions = len(risk_bearing)

        # Constraint 1: Single position per asset
        if any(p['symbol'] == trade['symbol'] for p in active_positions):
            blocked_trades.append({'trade': trade, 'reason': 'SYMBOL_ALREADY_ACTIVE'})
            continue

        # Constraint 2: Max 3 slots
        if len(risk_bearing) >= 3:
            blocked_trades.append({'trade': trade, 'reason': 'MAX_SLOTS_REACHED'})
            continue

        # Constraint 3: Market Analyser Regime Filter (if enabled)
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

            if last_strategy != trade['strategy']:
                switches.append({'time': t_entry, 'from': last_strategy, 'to': trade['strategy']})
                last_strategy = trade['strategy']
        else:
            # Without Market Analyser: Pre-trade daily circuit breaker check only
            cb_ok, cb_reason = guardian.check_can_open_trade(trade['risk_dollars'], balance)
            if not cb_ok:
                blocked_trades.append({'trade': trade, 'reason': f'CB_HALT: {cb_reason}'})
                continue
            if last_strategy != trade['strategy']:
                switches.append({'time': t_entry, 'from': last_strategy, 'to': trade['strategy']})
                last_strategy = trade['strategy']

        # Execute trade
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

if __name__ == '__main__':
    all_candidates = generate_all_raw_candidates()
    print(f"\nGenerated {len(all_candidates)} Total Raw Candidates.")

    print("\n[Running Mode A: WITH Market Analyser...]")
    res_with = simulate_portfolio(all_candidates, use_market_analyser=True)

    print("[Running Mode B: WITHOUT Market Analyser (Raw Simultaneous 3-Slot Replay)...]")
    res_without = simulate_portfolio(all_candidates, use_market_analyser=False)

    print("\n" + "=" * 115)
    print("      HEAD-TO-HEAD AUDIT: WITH MARKET ANALYSER vs WITHOUT MARKET ANALYSER (SIMULTANEOUS)")
    print("      (100% Raw Parquet Bar Data Replay | Exact Same Raw Candidate Pool | Starting Capital: $5,000)")
    print("=" * 115)
    print(f"{'Performance Metric':<38} | {'WITH Market Analyser':<35} | {'WITHOUT Market Analyser':<35}")
    print("-" * 115)
    print(f"{'Total Candidates Evaluated':<38} | {len(all_candidates):<35} | {len(all_candidates):<35}")
    print(f"{'Executed Trades':<38} | {res_with['total_executed']:<35} | {res_without['total_executed']:<35}")
    print(f"{'Blocked Signals':<38} | {res_with['total_blocked']} (Regime + Slot Caps) | {res_without['total_blocked']} (Slots Only)")
    print(f"{'Portfolio Win Rate':<38} | {res_with['win_rate']:.2f}% ({res_with['wins']}W / {res_with['losses']}L) | {res_without['win_rate']:.2f}% ({res_without['wins']}W / {res_without['losses']}L)")
    print(f"{'Total Net Profit':<38} | +${res_with['net_profit']:,.2f} (+{res_with['net_profit']/50:.1f}%) | +${res_without['net_profit']:,.2f} (+{res_without['net_profit']/50:.1f}%)")
    print(f"{'Profit Factor (PF)':<38} | {res_with['profit_factor']:.2f} | {res_without['profit_factor']:.2f}")
    print(f"{'Ending Account Balance':<38} | ${res_with['final_balance']:,.2f} | ${res_without['final_balance']:,.2f}")
    print(f"{'Max Peak-to-Trough Drawdown':<38} | ${res_with['max_dd_dollars']:,.2f} ({res_with['max_dd_pct']:.2f}%) | ${res_without['max_dd_dollars']:,.2f} ({res_without['max_dd_pct']:.2f}%)")
    print(f"{'Worst Single-Day Drop':<38} | ${res_with['max_daily_dd_dollars']:,.2f} ({res_with['max_daily_dd_pct']:.2f}%) | ${res_without['max_daily_dd_dollars']:,.2f} ({res_without['max_daily_dd_pct']:.2f}%)")
    print(f"{'Strategy Switches Executed':<38} | {res_with['switches_count']} active switches | {res_without['switches_count']} interleaved switches")
    print("-" * 115)
    print(f"{'ApexHunter PnL (Trades, WR, PF)':<38} | +${res_with['apex_pnl']:,.2f} ({res_with['apex_trades']} trds, {res_with['apex_wr']:.1f}%, {res_with['apex_pf']:.2f}) | +${res_without['apex_pnl']:,.2f} ({res_without['apex_trades']} trds, {res_without['apex_wr']:.1f}%, {res_without['apex_pf']:.2f})")
    print(f"{'Lewis Kelly PnL (Trades, WR, PF)':<38} | +${res_with['lk_pnl']:,.2f} ({res_with['lk_trades']} trds, {res_with['lk_wr']:.1f}%, {res_with['lk_pf']:.2f}) | +${res_without['lk_pnl']:,.2f} ({res_without['lk_trades']} trds, {res_without['lk_wr']:.1f}%, {res_without['lk_pf']:.2f})")
    print("=" * 115)

    # Save comparison dataframes for inspection
    res_with['df_exec'].to_csv('backtest/with_market_analyser_trades.csv', index=False)
    res_without['df_exec'].to_csv('backtest/without_market_analyser_trades.csv', index=False)
