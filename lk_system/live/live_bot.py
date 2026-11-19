"""
Live Bot Launcher (Redirects to Flagship Production Master)
===========================================================
Location: live/live_bot.py

This launcher routes directly to the unified, production-grade master bot
(ApexHunter v1.2.1 + Lewis Kelly SMC Basket with Dual Circuit Breakers,
10m News Auto-Close, Golden Spread Caps, and Server-Side Split Orders).
"""

import sys
import os

CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.abspath(os.path.join(CURRENT_DIR, ".."))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from master_live_bot import MasterUnifiedBot, main

if __name__ == '__main__':
    main()
