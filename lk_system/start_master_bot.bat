@echo off
TITLE Institutional Master Trading Bot - Launch Console
COLOR 0A

:MENU
cls
echo ===============================================================================
echo                INSTITUTIONAL MASTER UNIFIED TRADING BOT (MT5)
echo           ApexHunter v1.2.1 (XAUUSD / NAS100) + Lewis Kelly SMC (6 Forex)
echo ===============================================================================
echo.
echo   1. Start Bot in PAPER / DRY-RUN Mode (Safe Live Market Forward Test)
echo   2. Start Bot in REAL LIVE MT5 EXECUTION Mode (--live)
echo   3. Run 100% Strategy Logic & Indicator Diagnostic Suite (--test-strategy)
echo   4. Run Diagnostic MT5 Smoke Test (tests/test_master_bot_smoke.py)
echo   5. Exit
echo.
echo ===============================================================================
set /p M="Select an option (1-5) and press ENTER: "

if %M%==1 goto DRY_RUN
if %M%==2 goto LIVE_MODE
if %M%==3 goto DIAGNOSTICS
if %M%==4 goto SMOKE_TEST
if %M%==5 goto EOF

:DRY_RUN
cls
echo [STARTING] Launching Master Bot in PAPER TRADING Mode...
python -u master_live_bot.py
pause
goto MENU

:LIVE_MODE
cls
echo ===============================================================================
echo [CAUTION] You are about to launch LIVE ORDER EXECUTION on MetaTrader 5!
echo Real orders will be sent to your connected broker account.
echo ===============================================================================
set /p CONFIRM="Are you sure you want to proceed? (Type YES to confirm): "
if /I "%CONFIRM%"=="YES" (
    cls
    echo [STARTING] Launching Master Bot in REAL LIVE MT5 EXECUTION Mode...
    python -u master_live_bot.py --live
) else (
    echo [ABORTED] Live execution aborted by user.
)
pause
goto MENU

:DIAGNOSTICS
cls
echo [RUNNING] Executing 100% Strategy Logic & Indicator Diagnostic Suite...
python -u master_live_bot.py --test-strategy
pause
goto MENU

:SMOKE_TEST
cls
echo [RUNNING] Executing Diagnostic MT5 Smoke Test...
python -u tests/test_master_bot_smoke.py
pause
goto MENU

:EOF
exit

