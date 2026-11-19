import MetaTrader5 as mt5
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import pytz
from swing_analyzer import SwingAnalyzer

def get_data(symbol, timeframe, start, end, tz):
    rates = mt5.copy_rates_range(symbol, timeframe, start, end)
    if rates is None or len(rates) == 0:
        return pd.DataFrame()
    df = pd.DataFrame(rates)
    df['time'] = pd.to_datetime(df['time'], unit='s', utc=True)
    df.set_index('time', inplace=True)
    df = df.tz_convert(tz)
    return df

def run_swing_backtest():
    mt5.initialize()
    tz = pytz.timezone("Etc/UTC")
    
    # Fetch from June for warmup data
    start_date = datetime(2026, 6, 1, tzinfo=tz)
    end_date = datetime(2026, 7, 31, tzinfo=tz)
    aug_start = datetime(2026, 7, 1, tzinfo=tz)
    
    symbol = "EURUSD"
    print(f"Fetching data from June to end of July for {symbol}...")
    
    df_h4 = get_data(symbol, mt5.TIMEFRAME_H4, start_date, end_date, tz)
    df_h1 = get_data(symbol, mt5.TIMEFRAME_H1, start_date, end_date, tz)
    
    if df_h4.empty or df_h1.empty:
        print("Failed to get MT5 data.")
        return
        
    print(f"Loaded {len(df_h4)} H4 candles and {len(df_h1)} H1 candles.")
    
    analyzer = SwingAnalyzer(h4_lookback=5, h1_lookback=3)
    
    # We will simulate trading day by day, but since it's a swing bot, we can just process
    # the entire history using a rolling window to avoid lookahead bias.
    
    trades = []
    in_trade = False
    entry_price = 0
    sl = 0
    tp = 0
    trade_type = ""
    entry_time = None
    
    print("Running simulation...")
    
    # We need to iterate through H1 candles. At each H1 candle, we have access to H4 candles UP TO that point.
    for i in range(100, len(df_h1)):
        current_time = df_h1.index[i]
        
        # Current H1 bar
        curr_close = df_h1['close'].iloc[i]
        curr_high = df_h1['high'].iloc[i]
        curr_low = df_h1['low'].iloc[i]
        
        # Manage active trade
        if in_trade:
            # Check SL/TP
            if trade_type == 'BUY':
                if curr_low <= sl:
                    trades.append({'time': current_time, 'type': 'BUY', 'pnl': -1.0})
                    in_trade = False
                elif curr_high >= tp:
                    trades.append({'time': current_time, 'type': 'BUY', 'pnl': 5.0}) # 5R target
                    in_trade = False
            elif trade_type == 'SELL':
                if curr_high >= sl:
                    trades.append({'time': current_time, 'type': 'SELL', 'pnl': -1.0})
                    in_trade = False
                elif curr_low <= tp:
                    trades.append({'time': current_time, 'type': 'SELL', 'pnl': 5.0})
                    in_trade = False
            continue
            
        # Get historical data up to this point
        # To avoid lookahead, we only take H4 candles that CLOSED before this H1 candle
        historical_h4 = df_h4[df_h4.index < current_time]
        if len(historical_h4) < 50:
            continue
            
        if i % 500 == 0:
            print(f"Processed {i}/{len(df_h1)} H1 candles...", flush=True)
            
        # 1. Process H4 Macro Bias
        bias, poi = analyzer.process_h4(historical_h4)
        
        if not poi:
            continue
            
        # 2. Check H1 CHoCH Entry
        # Take the last 50 H1 candles
        historical_h1 = df_h1.iloc[i-50:i]
        
        entry_signal = analyzer.check_h1_entry(historical_h1, bias, poi)
        
        if entry_signal and current_time >= aug_start:
            in_trade = True
            trade_type = entry_signal['signal']
            entry_price = entry_signal['entry_price']
            sl = entry_signal['sl']
            
            risk = abs(entry_price - sl)
            if trade_type == 'BUY':
                tp = entry_price + (risk * 5.0) # 5R
            else:
                tp = entry_price - (risk * 5.0)
                
            entry_time = current_time
            
    print("\n=== SWING BOT BACKTEST RESULTS (1 YEAR) ===")
    total_trades = len(trades)
    if total_trades > 0:
        wins = len([t for t in trades if t['pnl'] > 0])
        win_rate = (wins / total_trades) * 100
        total_r = sum([t['pnl'] for t in trades])
        
        print(f"Total Trades: {total_trades}")
        print(f"Win Rate: {win_rate:.1f}%")
        print(f"Net Profit (R): {total_r:.2f} R")
    else:
        print("No trades taken.")

if __name__ == "__main__":
    run_swing_backtest()
