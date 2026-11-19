import os
import sys
import time
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Any
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
from dcc_engine_v1_2 import DCCEngineV12

def run_unlimited_slots_backtest():
    print("=" * 115)
    print("      100% INDEPENDENT UNIFIED BACKTEST: UNLIMITED SLOTS + 2.5R FOREX BREAKEVEN")
    print("      (Zero Market Analyser | Raw Simultaneous Execution | 100% Calculated from Parquet Price Bars)")
    print("=" * 115)

    # -------------------------------------------------------------------------
    # 1. GENERATE LEWIS KELLY SMC TRADES WITH 2.5R BREAKEVEN TRIGGER
    # -------------------------------------------------------------------------
    print("\n[Phase 1/3] Generating Lewis Kelly SMC trades with 2.5R Breakeven Trigger...")
    t0 = time.time()
    lk_trades = []
    for pair, cfg in PAIRS_CONFIG.items():
        engine = FixedRiskForexEngine(symbol=pair, config=cfg, fixed_dollar_risk=50.0, tier1_rr=2.5)
        engine.run_backtest()
        for t in engine.trades:
            entry_t = pd.to_datetime(t['entry_time'], utc=True)
            exit_t = pd.to_datetime(t['exit_time'], utc=True) if t.get('exit_time') else entry_t + pd.Timedelta(hours=4)
            be_t = pd.to_datetime(t['be_time'], utc=True) if t.get('be_time') else None

            lk_trades.append({
                'strategy': 'LEWIS_KELLY_SMC',
                'symbol': pair,
                'direction': t['type'].upper(),
                'entry_time': entry_t,
                'exit_time': exit_t,
                'be_time': be_t,
                'pnl': round(t['pnl'], 2),
                'risk_dollars': 50.0,
            })
    print(f"  Lewis Kelly: {len(lk_trades)} trades generated in {round(time.time() - t0, 1)}s. Net: ${sum(t['pnl'] for t in lk_trades):,.2f}")

    # -------------------------------------------------------------------------
    # 2. GENERATE APEXHUNTER v1.2.1 MODEL B TRADES FROM RAW PARQUET BARS
    # -------------------------------------------------------------------------
    print("\n[Phase 2/3] Generating ApexHunter v1.2.1 Model B trades on Gold & Nasdaq from raw Parquet bars...")
    t1 = time.time()
    apex_specs = {
        'XAUUSD': {'pip': 0.10, 'contract': 100.0, 'comm_lot': 5.0, 'sl_mult': 1.30, 'tp1_rr': 1.40, 'tp2_rr': 2.10},
        'NAS100': {'pip': 1.0,  'contract': 10.0,  'comm_lot': 5.0, 'sl_mult': 1.40, 'tp1_rr': 1.40, 'tp2_rr': 2.10}
    }
    broker_offset = timedelta(hours=3)
    apex_trades = []

    for sym, spec in apex_specs.items():
        val_pt = spec['contract'] * spec['pip']

        df_m5_p1 = pd.read_parquet(os.path.join(DC_ROOT, 'data_cache', f'm5_bars_{sym}.parquet'))
        df_m5_p1['time_utc'] = (pd.to_datetime(df_m5_p1['time'], unit='s', utc=True) - broker_offset).astype('datetime64[us, UTC]')
        df_m5_p1.set_index('time_utc', inplace=True)
        df_m5_p1.rename(columns={'tick_volume': 'volume'}, inplace=True)

        df_h1_p1 = pd.read_parquet(os.path.join(DC_ROOT, 'data_cache', f'h1_bars_{sym}.parquet'))
        df_h1_p1['time_utc'] = (pd.to_datetime(df_h1_p1['time'], unit='s', utc=True) - broker_offset).astype('datetime64[us, UTC]')
        df_h1_p1.set_index('time_utc', inplace=True)

        p2_m5 = os.path.join(DC_ROOT, 'data_cache', f'm5_bars_{sym}_oct_nov_2026.parquet')
        p2_h1 = os.path.join(DC_ROOT, 'data_cache', f'h1_bars_{sym}_oct_nov_2026.parquet')
        if os.path.exists(p2_m5) and os.path.exists(p2_h1):
            df_m5_p2 = pd.read_parquet(p2_m5)
            df_m5_p2.index = (pd.to_datetime(df_m5_p2.index, utc=True) - broker_offset).astype('datetime64[us, UTC]')
            df_m5_p2.rename(columns={'tick_volume': 'volume'}, inplace=True)
            df_h1_p2 = pd.read_parquet(p2_h1)
            df_h1_p2.index = (pd.to_datetime(df_h1_p2.index, utc=True) - broker_offset).astype('datetime64[us, UTC]')
            df_m5_all = pd.concat([df_m5_p1, df_m5_p2]).sort_index()
            df_h1_all = pd.concat([df_h1_p1, df_h1_p2]).sort_index()
        else:
            df_m5_all = df_m5_p1
            df_h1_all = df_h1_p1

        engine = DCCEngineV12(symbol=sym, atr_sl_multiplier=spec['sl_mult'], tp1_rr=spec['tp1_rr'], tp2_rr=spec['tp2_rr'])
        df_prep = engine.prepare_data(df_m5_all, df_h1_all)
        df_prep['p_diff'] = (df_prep['ema9_5m'] - df_prep['ema20_5m']).shift(1)
        df_prep['c_diff'] = df_prep['ema9_5m'] - df_prep['ema20_5m']

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
                    apex_trades.append(pos)
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
                    apex_trades.append(pos)
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
                        'risk_dollars': 50.0
                    }

    print(f"  ApexHunter: {len(apex_trades)} trades generated in {round(time.time() - t1, 1)}s. Net: ${sum(t['pnl'] for t in apex_trades):,.2f}")

    # -------------------------------------------------------------------------
    # 3. UNLIMITED SLOTS PORTFOLIO SIMULATION (CHRONOLOGICAL TIME-TRAVERSAL)
    # -------------------------------------------------------------------------
    all_candidates = sorted(lk_trades + apex_trades, key=lambda x: x['entry_time'])
    print(f"\n[Phase 3/3] Simulating Unified Portfolio with UNLIMITED SLOTS across {len(all_candidates)} candidates...")

    starting_capital = 5000.0
    balance = starting_capital
    peak_balance = starting_capital
    max_equity_dd_dollars = 0.0
    max_equity_dd_pct = 0.0

    current_day = None
    day_start_balance = starting_capital
    max_daily_dd_dollars = 0.0
    max_daily_dd_pct = 0.0

    max_simultaneous_open = 0
    max_simultaneous_risk_bearing = 0

    active_positions = []
    executed_trades = []
    blocked_trades = []
    concurrent_history = []

    for trade in all_candidates:
        t_entry = trade['entry_time']
        t_day = t_entry.date()

        # Daily balance tracking
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

        # Calculate risk-bearing vs total open
        risk_bearing = [p for p in active_positions if p.get('be_time') is None or p['be_time'] > t_entry]

        # Single position per symbol constraint (broker reality)
        if any(p['symbol'] == trade['symbol'] for p in active_positions):
            blocked_trades.append({'trade': trade, 'reason': 'SYMBOL_ALREADY_ACTIVE'})
            continue

        # UNLIMITED SLOTS: NO restriction on len(active_positions) or len(risk_bearing)!

        # Update peak concurrent positions stats
        curr_total_open = len(active_positions) + 1
        curr_risk_open = len(risk_bearing) + 1
        if curr_total_open > max_simultaneous_open:
            max_simultaneous_open = curr_total_open
        if curr_risk_open > max_simultaneous_risk_bearing:
            max_simultaneous_risk_bearing = curr_risk_open

        # Execute trade
        pnl = trade['pnl']
        balance += pnl
        if balance > peak_balance: peak_balance = balance
        eq_dd = peak_balance - balance
        eq_dd_pct = (eq_dd / peak_balance) * 100.0
        if eq_dd > max_equity_dd_dollars:
            max_equity_dd_dollars = eq_dd
            max_equity_dd_pct = eq_dd_pct

        trade_exec = dict(trade)
        trade_exec['balance_after'] = round(balance, 2)
        trade_exec['open_trades_at_entry'] = curr_total_open
        trade_exec['risk_trades_at_entry'] = curr_risk_open
        active_positions.append(trade_exec)
        executed_trades.append(trade_exec)

    df_exec = pd.DataFrame(executed_trades)
    df_exec.to_csv('backtest/unlimited_slots_2_5rr_executed_trades.csv', index=False)

    wins = (df_exec['pnl'] > 0).sum()
    loss = (df_exec['pnl'] < 0).sum()
    wr = (wins / len(df_exec) * 100.0) if len(df_exec) > 0 else 0.0
    net_pnl = df_exec['pnl'].sum()
    gw = df_exec[df_exec['pnl'] > 0]['pnl'].sum()
    gl = abs(df_exec[df_exec['pnl'] < 0]['pnl'].sum())
    pf = (gw / gl) if gl > 0 else np.nan

    apex_df = df_exec[df_exec['strategy'] == 'APEXHUNTER_1_2_1']
    lk_df = df_exec[df_exec['strategy'] == 'LEWIS_KELLY_SMC']

    # Cross-strategy overlap analysis
    overlaps = 0
    hedged = 0
    both_win = 0
    both_loss = 0
    comb_overlap_pnl = 0.0

    for i in range(len(df_exec)):
        r_a = df_exec.iloc[i]
        for j in range(i + 1, len(df_exec)):
            r_b = df_exec.iloc[j]
            if pd.to_datetime(r_b['entry_time']) < pd.to_datetime(r_a['exit_time']):
                if r_a['strategy'] != r_b['strategy']:
                    overlaps += 1
                    comb_overlap_pnl += (r_a['pnl'] + r_b['pnl'])
                    if r_a['pnl'] * r_b['pnl'] < 0: hedged += 1
                    elif r_a['pnl'] > 0 and r_b['pnl'] > 0: both_win += 1
                    else: both_loss += 1
            else:
                break

    print("\n" + "=" * 115)
    print("      FINAL SCORECARD: 100% INDEPENDENT UNIFIED BACKTEST (UNLIMITED SLOTS + 2.5R BE)")
    print("      (Zero Market Analyser | Raw Simultaneous Execution | Starting Capital: $5,000)")
    print("=" * 115)
    print(f"  Starting Balance:                   $5,000.00")
    print(f"  Final Ending Balance:               ${balance:,.2f}")
    print(f"  Total Net Profit:                  +${net_pnl:,.2f} (+{(net_pnl/5000.0)*100:.2f}%)")
    print(f"  Total Signals Evaluated:            {len(all_candidates)}")
    print(f"  Trades Executed (Unlimited Slots):  {len(df_exec)} ({(len(df_exec)/len(all_candidates))*100:.1f}%)")
    print(f"  Trades Blocked (Same Symbol Only):  {len(blocked_trades)}")
    print(f"  Portfolio Win Rate:                 {wr:.2f}% ({wins} Wins / {loss} Losses)")
    print(f"  Portfolio Profit Factor:            {pf:.2f}")
    print("-" * 115)
    print("  CONCURRENCY & SLOT OCCUPANCY (UNLIMITED SLOTS DATA):")
    print(f"  * Peak Concurrent Total Open Trades:       {max_simultaneous_open} positions (Out of 8 max possible assets)")
    print(f"  * Peak Concurrent Risk-Bearing Trades:     {max_simultaneous_risk_bearing} positions (Dynamic BE liberated the rest!)")
    print(f"  * Total Cross-Strategy Overlapping Pairs:   {overlaps} instances")
    print(f"  * Natural Cross-Asset Hedging Rate:        {hedged/overlaps*100:.1f}% ({hedged} hedged | {both_win} both won | {both_loss} both lost)")
    print(f"  * Net Profit Banked during Overlaps Alone: +${comb_overlap_pnl:,.2f}")
    print("-" * 115)
    print("  DRAWDOWN & SAFETY METRICS:")
    print(f"  * Max Peak-to-Trough Drawdown:      ${max_equity_dd_dollars:,.2f} ({max_equity_dd_pct:.2f}%) [Prop Firm Limit: 10.0% / $500]")
    print(f"  * Worst Single-Day Drop:            ${max_daily_dd_dollars:,.2f} ({max_daily_dd_pct:.2f}%) [Prop Firm Limit: 5.0% / $250]")
    print("-" * 115)
    print("  STRATEGY CONTRIBUTION BREAKDOWN:")
    print(f"  * ApexHunter v1.2.1 (Gold & Nasdaq): {len(apex_df)} trades | {(apex_df['pnl']>0).mean()*100:.1f}% WR | PF {apex_df[apex_df['pnl']>0]['pnl'].sum()/abs(apex_df[apex_df['pnl']<0]['pnl'].sum()):.2f} | Net: +${apex_df['pnl'].sum():,.2f}")
    print(f"  * Lewis Kelly SMC (6 Forex Pairs)  : {len(lk_df)} trades | {(lk_df['pnl']>0).mean()*100:.1f}% WR | PF {lk_df[lk_df['pnl']>0]['pnl'].sum()/abs(lk_df[lk_df['pnl']<0]['pnl'].sum()):.2f} | Net: +${lk_df['pnl'].sum():,.2f}")
    print("=" * 115)

if __name__ == '__main__':
    run_unlimited_slots_backtest()
