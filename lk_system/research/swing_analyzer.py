import pandas as pd
import numpy as np

class SwingAnalyzer:
    def __init__(self, h4_lookback=5, h1_lookback=3):
        self.h4_lookback = h4_lookback
        self.h1_lookback = h1_lookback

    def identify_swings(self, df, lookback):
        highs = []
        lows = []
        if len(df) < 2 * lookback + 1:
            return highs, lows
            
        high_vals = df['high'].values
        low_vals = df['low'].values
        times = df.index
        
        for i in range(lookback, len(df) - lookback):
            is_swing_high = True
            is_swing_low = True
            
            for j in range(1, lookback + 1):
                if high_vals[i] <= high_vals[i-j] or high_vals[i] <= high_vals[i+j]:
                    is_swing_high = False
                if low_vals[i] >= low_vals[i-j] or low_vals[i] >= low_vals[i+j]:
                    is_swing_low = False
                    
            if is_swing_high:
                highs.append({'index': i, 'time': times[i], 'price': high_vals[i], 'type': 'high'})
            if is_swing_low:
                lows.append({'index': i, 'time': times[i], 'price': low_vals[i], 'type': 'low'})
                
        return highs, lows

    def process_h4(self, df_h4):
        highs, lows = self.identify_swings(df_h4, self.h4_lookback)
        all_swings = sorted(highs + lows, key=lambda x: x['index'])
        
        bias = "NEUTRAL"
        poi = None
        
        last_high = None
        last_low = None
        
        # Traverse swings to find the LATEST BOS and current active POI
        for i, swing in enumerate(all_swings):
            if swing['type'] == 'high':
                if last_high and swing['price'] > last_high['price'] and last_low:
                    # Bullish BOS: Price broke above the previous high.
                    bias = "BULLISH"
                    # The POI is the unmitigated zone around the last low that caused the break
                    poi = {'type': 'bullish', 'time': last_low['time'], 'price': last_low['price'], 'broken_level': last_high['price']}
                last_high = swing
            else:
                if last_low and swing['price'] < last_low['price'] and last_high:
                    # Bearish BOS: Price broke below the previous low.
                    bias = "BEARISH"
                    # The POI is the unmitigated zone around the last high that caused the break
                    poi = {'type': 'bearish', 'time': last_high['time'], 'price': last_high['price'], 'broken_level': last_low['price']}
                last_low = swing
                
        return bias, poi
    
    def check_h1_entry(self, df_h1, h4_bias, h4_poi):
        """
        Check if the H1 chart presents a valid CHoCH entry in the direction of the H4 bias,
        AFTER price has tapped the H4 POI.
        """
        if not h4_poi or h4_bias == "NEUTRAL":
            return None
            
        # 1. Did H1 tap the H4 POI recently? 
        highs, lows = self.identify_swings(df_h1, self.h1_lookback)
        all_h1 = sorted(highs + lows, key=lambda x: x['index'])
        
        last_h1_high = None
        last_h1_low = None
        
        for swing in all_h1:
            if swing['type'] == 'high':
                if h4_bias == "BULLISH" and last_h1_high and swing['price'] > last_h1_high['price']:
                    # H1 Bullish CHoCH (breaking a previous lower high)
                    if last_h1_low and abs(last_h1_low['price'] - h4_poi['price']) < (h4_poi['broken_level'] - h4_poi['price']) * 0.4:
                        return {
                            'signal': 'BUY',
                            'entry_price': df_h1['close'].iloc[-1], # approximate current price
                            'sl': last_h1_low['price']
                        }
                last_h1_high = swing
            else:
                if h4_bias == "BEARISH" and last_h1_low and swing['price'] < last_h1_low['price']:
                    # H1 Bearish CHoCH
                    if last_h1_high and abs(last_h1_high['price'] - h4_poi['price']) < (h4_poi['price'] - h4_poi['broken_level']) * 0.4:
                        return {
                            'signal': 'SELL',
                            'entry_price': df_h1['close'].iloc[-1],
                            'sl': last_h1_high['price']
                        }
                last_h1_low = swing
                
        return None
