import os
import sys
import time
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Any
import pandas as pd
import numpy as np
import pickle

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
if PROJECT_ROOT not in sys.path: sys.path.insert(0, PROJECT_ROOT)
if PROJECT_ROOT not in sys.path: sys.path.insert(0, PROJECT_ROOT)

from lk_system.backtest.test_forex_basket_fixed_risk import FixedRiskForexEngine, PAIRS_CONFIG

def run_breakeven_sweep():
    test_levels = [1.2, 1.5, 1.8, 2.0, 2.2, 2.5, 2.8, 3.0, 3.5]
    results = []

    print("=" * 115)
    print("      QUANTITATIVE RESEARCH AUDIT: FOREX BREAKEVEN (BE) R:R TRIGGER SWEEP")
    print("      Testing Across All 6 Major Forex Pairs (EURUSD, GBPUSD, USDJPY, AUDUSD, EURJPY, USDCAD)")
    print("=" * 115)

    for be_rr in test_levels:
        t0 = time.time()
        total_pnl = 0.0
        total_trades = 0
        total_wins = 0
        total_losses = 0
        gross_win = 0.0
        gross_loss = 0.0
        be_exits = 0
        full_runners_8r = 0
        time_to_be_list = []

        for pair, cfg in PAIRS_CONFIG.items():
            engine = FixedRiskForexEngine(symbol=pair, config=cfg, fixed_dollar_risk=50.0, tier1_rr=be_rr)
            engine.run_backtest()
            for t in engine.trades:
                total_trades += 1
                pnl = t['pnl']
                total_pnl += pnl
                if pnl > 0:
                    total_wins += 1
                    gross_win += pnl
                else:
                    total_losses += 1
                    gross_loss += abs(pnl)

                if t.get('tier_8.0', False):
                    full_runners_8r += 1
                elif t.get(f'tier_{be_rr}', False) and pnl < 100:
                    be_exits += 1

                if t.get('be_time') and t.get('entry_time'):
                    # Convert to hours
                    hold_h = (t['be_time'] - t['entry_time']).total_seconds() / 3600.0
                    time_to_be_list.append(hold_h)

        wr = (total_wins / total_trades * 100.0) if total_trades > 0 else 0.0
        pf = (gross_win / gross_loss) if gross_loss > 0 else 0.0
        avg_be_time_h = np.mean(time_to_be_list) if time_to_be_list else 0.0

        results.append({
            'be_rr': be_rr,
            'net_pnl': total_pnl,
            'trades': total_trades,
            'win_rate': wr,
            'pf': pf,
            'be_exits': be_exits,
            'runners_8r': full_runners_8r,
            'avg_be_time_h': avg_be_time_h,
            'elapsed': time.time() - t0
        })
        note = "(Apex Style)" if be_rr == 1.5 else ("(Current Baseline)" if be_rr == 3.0 else "")
        print(f"BE Trigger = {be_rr:>4.1f}R {note:<18} | Net: ${total_pnl:>9,.2f} | WR: {wr:>5.1f}% | PF: {pf:>4.2f} | 8R Runners: {full_runners_8r:>2} | Avg Time to Free Slot: {avg_be_time_h:>4.1f}h")

    df_res = pd.DataFrame(results)
    print("\n" + "=" * 115)
    print("      FINAL COMPARATIVE SCORECARD: WHAT IS THE MATHEMATICALLY OPTIMAL FOREX BREAKEVEN RR?")
    print("=" * 115)
    print(f"{'Tier 1 BE Trigger':<20} | {'Total Net PnL':<15} | {'Win Rate':<10} | {'Profit Factor':<14} | {'8R Runners Banked':<18} | {'Avg Time to Free Slot':<20}")
    print("-" * 115)
    for idx, r in df_res.iterrows():
        lbl = f"{r['be_rr']:.1f}R"
        if r['be_rr'] == 1.5: lbl += " (Apex Style)"
        elif r['be_rr'] == 3.0: lbl += " (Current LK)"
        print(f"{lbl:<20} | ${r['net_pnl']:>10,.2f}    | {r['win_rate']:>6.1f}%   | {r['pf']:>6.2f}         | {int(r['runners_8r']):>3} runners        | {r['avg_be_time_h']:>5.1f} hours")
    print("=" * 115)

    df_res.to_csv('backtest/forex_breakeven_rr_sweep_results.csv', index=False)
    print("\nSaved results to backtest/forex_breakeven_rr_sweep_results.csv")

if __name__ == '__main__':
    run_breakeven_sweep()
