"""
ApexHunter 2.0 — Production Root Launcher
=========================================
Launches the ApexHunter 2.0 Institutional Multi-Asset Trading Bot from the repository root.

Usage:
  python run_apexhunter_2.py --live                   # Run live MT5 execution
  python run_apexhunter_2.py --dry-run                # Run live surveillance in dry-run paper trading mode
  python run_apexhunter_2.py --auto -y                # Bypass interactive prompt and run immediately
  python run_apexhunter_2.py --daily-cb 4.0           # Set 4.0% Daily Drawdown Circuit Breaker limit
  python run_apexhunter_2.py --max-dd 10.0            # Set 10.0% Max Total Drawdown Hard Floor limit
  python run_apexhunter_2.py --floor-cb 8.5           # Set 8.5% Recovery Floor Circuit Breaker threshold
  python run_apexhunter_2.py --risk 1.0               # Set 1.0% trade risk percentage
"""

import sys
import os

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
LK_DIR = os.path.join(PROJECT_ROOT, "lk_system")

if LK_DIR not in sys.path:
    sys.path.insert(0, LK_DIR)
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from master_live_bot import main

if __name__ == "__main__":
    main()
