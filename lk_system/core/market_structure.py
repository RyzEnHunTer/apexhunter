import pandas as pd
import numpy as np

class MarketStructure:
    """
    True Lewis Kelly SMC Market Structure Engine.
    
    Implements:
    1. Strict 3-Candle Pullback Rule:
       - Confirmed Swing High: Peak before at least 3 consecutive opposing (bearish) candles.
       - Confirmed Swing Low: Trough before at least 3 consecutive opposing (bullish) candles.
    2. Strong (Protected) vs Weak (Targeted) Swings:
       - In an uptrend, the swing low that caused the BOS is the Strong Low (Protected).
       - In a downtrend, the swing high that caused the BOS is the Strong High (Protected).
    3. Iron Candlestick Closure Rule:
       - Break of Structure (BOS) / Change of Character (CHoCH) REQUIRES a full candle body close.
       - Wick-only breaches are classified as Liquidity Sweeps / Rejections, NOT structural breaks.
    """
    def __init__(self, pullback_candles=3):
        self.pullback_candles = pullback_candles

    def update(self, df: pd.DataFrame) -> pd.DataFrame:
        if df.empty:
            return df
            
        df = df.copy()
        n = len(df)
        
        opens = df['Open'].values
        highs = df['High'].values
        lows = df['Low'].values
        closes = df['Close'].values
        
        swing_high = np.zeros(n, dtype=bool)
        swing_low = np.zeros(n, dtype=bool)
        bos = np.zeros(n, dtype=bool)
        choch = np.zeros(n, dtype=bool)
        sweep_high = np.zeros(n, dtype=bool)
        sweep_low = np.zeros(n, dtype=bool)
        
        trend = np.full(n, 'none', dtype=object)
        last_break = np.full(n, 'None', dtype=object)
        
        strong_high_arr = np.full(n, np.nan)
        strong_low_arr = np.full(n, np.nan)
        weak_high_arr = np.full(n, np.nan)
        weak_low_arr = np.full(n, np.nan)
        
        # 1. Detect Swings using Lewis Kelly's 3-Candle Pullback Rule
        # In an uptrending push: 3 consecutive bearish candles (Close < Open and Close < prev Close) confirm swing high
        # In a downtrending push: 3 consecutive bullish candles (Close > Open and Close > prev Close) confirm swing low
        k = self.pullback_candles
        
        confirmed_swings = [] # List of tuples: (index, type: 'H'/'L', price, time)
        
        # Scan for swing points
        for i in range(k, n):
            # Check for 3-candle bearish pullback confirming a Swing High prior to the pullback
            is_bearish_pb = True
            for j in range(k):
                bar_idx = i - j
                # Bearish candle: Close < Open OR Close < previous Close
                if not (closes[bar_idx] < opens[bar_idx] or closes[bar_idx] < closes[bar_idx - 1]):
                    is_bearish_pb = False
                    break
            
            if is_bearish_pb:
                # Find the highest high in the run leading up to this pullback
                lookback_start = max(0, i - k - 10)
                peak_sub_idx = np.argmax(highs[lookback_start : i - k + 1])
                peak_idx = lookback_start + peak_sub_idx
                if not swing_high[peak_idx]:
                    swing_high[peak_idx] = True
                    confirmed_swings.append((peak_idx, 'H', highs[peak_idx]))
            
            # Check for 3-candle bullish pullback confirming a Swing Low prior to the pullback
            is_bullish_pb = True
            for j in range(k):
                bar_idx = i - j
                # Bullish candle: Close > Open OR Close > previous Close
                if not (closes[bar_idx] > opens[bar_idx] or closes[bar_idx] > closes[bar_idx - 1]):
                    is_bullish_pb = False
                    break
                    
            if is_bullish_pb:
                lookback_start = max(0, i - k - 10)
                trough_sub_idx = np.argmin(lows[lookback_start : i - k + 1])
                trough_idx = lookback_start + trough_sub_idx
                if not swing_low[trough_idx]:
                    swing_low[trough_idx] = True
                    confirmed_swings.append((trough_idx, 'L', lows[trough_idx]))
        
        # Map confirmed swings by confirmation index (s_idx + k) for O(1) instantaneous lookup
        swings_by_confirm_bar = {}
        for s_idx, s_type, s_price in confirmed_swings:
            confirm_bar = s_idx + k
            if confirm_bar not in swings_by_confirm_bar:
                swings_by_confirm_bar[confirm_bar] = []
            swings_by_confirm_bar[confirm_bar].append((s_type, s_price))
        
        # 2. Sequential Stateful Structure Tracking
        # State variables
        curr_trend = 'none'
        strong_h = np.nan
        strong_l = np.nan
        weak_h = np.nan
        weak_l = np.nan
        
        last_swing_h = np.nan
        last_swing_l = np.nan
        curr_break = 'None'
        
        for i in range(n):
            c = closes[i]
            h = highs[i]
            l = lows[i]
            
            # Check if a new confirmed swing high/low was confirmed at this bar in O(1) time
            if i in swings_by_confirm_bar:
                for s_type, s_price in swings_by_confirm_bar[i]:
                    if s_type == 'H':
                        last_swing_h = s_price
                        if curr_trend == 'bullish':
                            weak_h = s_price
                    elif s_type == 'L':
                        last_swing_l = s_price
                        if curr_trend == 'bearish':
                            weak_l = s_price
            
            is_bos = False
            is_choch = False
            is_sw_h = False
            is_sw_l = False
            
            # Initialize trend if uninitialized
            if curr_trend == 'none':
                if not np.isnan(last_swing_h) and c > last_swing_h:
                    curr_trend = 'bullish'
                    is_choch = True
                    strong_l = last_swing_l if not np.isnan(last_swing_l) else l
                    weak_h = h
                elif not np.isnan(last_swing_l) and c < last_swing_l:
                    curr_trend = 'bearish'
                    is_choch = True
                    strong_h = last_swing_h if not np.isnan(last_swing_h) else h
                    weak_l = l
                    
            elif curr_trend == 'bullish':
                # Check for Break of Structure (BOS) -> Continuation above weak/active high
                target_high = weak_h if not np.isnan(weak_h) else last_swing_h
                if not np.isnan(target_high):
                    if c > target_high:
                        # Full Candle Body Close -> Confirmed BOS
                        is_bos = True
                        curr_trend = 'bullish'
                        # The swing low that produced this BOS becomes the new Strong Low
                        if not np.isnan(last_swing_l):
                            strong_l = last_swing_l
                        weak_h = h # Will be refined when next swing forms
                    elif h > target_high and c <= target_high:
                        # Wick Pierced but Body closed below -> Liquidity Sweep!
                        is_sw_h = True
                
                # Check for Change of Character (CHoCH) -> Reversal below Strong Low
                if not np.isnan(strong_l):
                    if c < strong_l:
                        # Full Candle Body Close below Strong Low -> Confirmed CHoCH
                        is_choch = True
                        curr_trend = 'bearish'
                        # The high that caused the break becomes the new Strong High
                        strong_h = last_swing_h if not np.isnan(last_swing_h) else h
                        weak_l = l
                    elif l < strong_l and c >= strong_l:
                        # Wick sweep of Strong Low
                        is_sw_l = True
                        
            elif curr_trend == 'bearish':
                # Check for Break of Structure (BOS) -> Continuation below weak/active low
                target_low = weak_l if not np.isnan(weak_l) else last_swing_l
                if not np.isnan(target_low):
                    if c < target_low:
                        # Full Candle Body Close -> Confirmed BOS
                        is_bos = True
                        curr_trend = 'bearish'
                        if not np.isnan(last_swing_h):
                            strong_h = last_swing_h
                        weak_l = l
                    elif l < target_low and c >= target_low:
                        # Wick Pierced but Body closed above -> Liquidity Sweep!
                        is_sw_l = True
                        
                # Check for Change of Character (CHoCH) -> Reversal above Strong High
                if not np.isnan(strong_h):
                    if c > strong_h:
                        # Full Candle Body Close above Strong High -> Confirmed CHoCH
                        is_choch = True
                        curr_trend = 'bullish'
                        strong_l = last_swing_l if not np.isnan(last_swing_l) else l
                        weak_h = h
                    elif h > strong_h and c <= strong_h:
                        # Wick sweep of Strong High
                        is_sw_h = True
                        
            if is_choch:
                curr_break = 'CHoCH'
            elif is_bos:
                curr_break = 'BoS'
                
            bos[i] = is_bos
            choch[i] = is_choch
            sweep_high[i] = is_sw_h
            sweep_low[i] = is_sw_l
            trend[i] = curr_trend
            last_break[i] = curr_break
            
            strong_high_arr[i] = strong_h
            strong_low_arr[i] = strong_l
            weak_high_arr[i] = weak_h
            weak_low_arr[i] = weak_l

        df['Swing_High'] = swing_high
        df['Swing_Low'] = swing_low
        df['BoS'] = bos
        df['CHoCH'] = choch
        df['Sweep_High'] = sweep_high
        df['Sweep_Low'] = sweep_low
        df['Trend'] = trend
        df['Last_Break'] = last_break
        
        df['Strong_High'] = strong_high_arr
        df['Strong_Low'] = strong_low_arr
        df['Weak_High'] = weak_high_arr
        df['Weak_Low'] = weak_low_arr
        
        # Backward compatibility for existing modules
        df['Active_High'] = np.where(pd.notna(strong_high_arr), strong_high_arr, weak_high_arr)
        df['Active_Low'] = np.where(pd.notna(strong_low_arr), strong_low_arr, weak_low_arr)
        
        return df
