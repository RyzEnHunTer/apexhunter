"""
ApexHunter 2.0 — Quick Launcher
===============================
Redirects to run_apexhunter_2.py.
"""
import sys
import os

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import run_apexhunter_2

if __name__ == "__main__":
    run_apexhunter_2.main()
