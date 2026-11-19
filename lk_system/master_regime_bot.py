"""
Master Regime Bot (Redirect Wrapper to Master Unified Bot)
==========================================================
Architecture Note:
Per our empirical backtest audit, NO Market Analyser or Regime Switcher is used.
Both strategies run simultaneously and 100% independently on their dedicated assets:
- ApexHunter v1.2.1 operates on Gold (XAUUSD) & Nasdaq (NAS100)
- Lewis Kelly SMC operates on the 6 Forex Pairs (EURUSD, GBPUSD, USDJPY, AUDUSD, EURJPY, USDCAD)

This file redirects cleanly to `master_live_bot.py`.
"""

from master_live_bot import MasterUnifiedBot, MasterRegimeBot, main

if __name__ == '__main__':
    main()
