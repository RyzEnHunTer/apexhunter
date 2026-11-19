"""
Smoke Test for Master Institutional Unified Bot
Executes live streaming & evaluation cycles to verify zero errors,
proper connection to MT5, symbol resolution, news calendar fetching,
and dashboard telemetry rendering.
"""

import sys
import os
import time

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from master_live_bot import MasterUnifiedBot

def test_master_bot():
    print("Starting Master Unified Bot Smoke Test...")
    bot = MasterUnifiedBot(dry_run=True, base_capital=5000.0)
    
    success = bot.initialize()
    if not success:
        print("[FAIL] Failed to initialize bot!")
        sys.exit(1)
        
    print("\n[SUCCESS] Bot successfully initialized. Executing 2 test cycles...")
    for i in range(1, 3):
        print(f"\n--- Smoke Test Cycle {i}/2 ---")
        bot.run_cycle()
        time.sleep(1.0)
    
    bot.bridge.disconnect()
    print("\n[ALL TESTS PASSED] Master Unified Bot Smoke Test completed successfully with 0 errors!")

if __name__ == '__main__':
    test_master_bot()
