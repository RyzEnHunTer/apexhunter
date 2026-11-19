import pandas as pd
import pytz
import numpy as np

class SessionManager:
    """
    Tracks Lewis Kelly's Session Killzones & Liquidity Extremes.
    Expects timestamps localized or convertible to US/Eastern timezone.
    
    Killzones (EST):
    - Asia Session: 19:00 - 01:00 (or 20:00 - 00:00)
    - London Killzone: 02:00 - 05:00 (2 AM - 5 AM)
    - New York Killzone: 07:00 - 10:00 (7 AM - 10 AM)
    """
    def __init__(self):
        self.reset()
        
    def reset(self):
        self.daily_sessions = {
            'Asia': {'high': 0.0, 'low': float('inf'), 'swept_high': False, 'swept_low': False},
            'London': {'high': 0.0, 'low': float('inf'), 'swept_high': False, 'swept_low': False},
            'NY': {'high': 0.0, 'low': float('inf'), 'swept_high': False, 'swept_low': False}
        }
        self.previous_ny = {'high': 0.0, 'low': float('inf')}
        self.daily_high = 0.0
        self.daily_low = float('inf')
        self.pdh = 0.0
        self.pdl = float('inf')
        self.current_date = None

    def _is_asia(self, dt):
        return dt.hour >= 19 or dt.hour < 1

    def _is_london(self, dt):
        return 2 <= dt.hour < 5

    def _is_ny(self, dt):
        return 7 <= dt.hour < 10

    def update(self, df: pd.DataFrame) -> pd.DataFrame:
        if df.empty:
            return df
            
        df = df.copy()
        n = len(df)
        
        active_session = np.full(n, 'None', dtype=object)
        asia_high = np.full(n, np.nan)
        asia_low = np.full(n, np.nan)
        london_high = np.full(n, np.nan)
        london_low = np.full(n, np.nan)
        ny_high = np.full(n, np.nan)
        ny_low = np.full(n, np.nan)
        
        pdh_arr = np.full(n, np.nan)
        pdl_arr = np.full(n, np.nan)
        
        # Immediate active bar sweeps (wick rejection)
        asia_swept_h = np.zeros(n, dtype=bool)
        asia_swept_l = np.zeros(n, dtype=bool)
        london_swept_h = np.zeros(n, dtype=bool)
        london_swept_l = np.zeros(n, dtype=bool)
        
        # Persistent state for the entire active session
        asia_ever_swept_h = np.zeros(n, dtype=bool)
        asia_ever_swept_l = np.zeros(n, dtype=bool)
        london_ever_swept_h = np.zeros(n, dtype=bool)
        london_ever_swept_l = np.zeros(n, dtype=bool)
        
        pdh_swept_arr = np.zeros(n, dtype=bool)
        pdl_swept_arr = np.zeros(n, dtype=bool)
        
        highs = df['High'].values
        lows = df['Low'].values
        closes = df['Close'].values
        timestamps = df.index
        
        self.reset()
        
        for i in range(n):
            dt = timestamps[i]
            curr_h = highs[i]
            curr_l = lows[i]
            curr_c = closes[i]
            
            # Forex trading day rolls over at 17:00 EST
            trade_day = dt.date() if dt.hour < 17 else (dt + pd.Timedelta(days=1)).date()
            
            if self.current_date != trade_day:
                self.current_date = trade_day
                self.previous_ny = {
                    'high': self.daily_sessions['NY']['high'],
                    'low': self.daily_sessions['NY']['low']
                }
                self.pdh = self.daily_high
                self.pdl = self.daily_low
                self.daily_high = 0.0
                self.daily_low = float('inf')
                
                self.daily_sessions = {
                    'Asia': {'high': 0.0, 'low': float('inf'), 'swept_high': False, 'swept_low': False},
                    'London': {'high': 0.0, 'low': float('inf'), 'swept_high': False, 'swept_low': False},
                    'NY': {'high': 0.0, 'low': float('inf'), 'swept_high': False, 'swept_low': False}
                }
                
            if curr_h > self.daily_high:
                self.daily_high = curr_h
            if curr_l < self.daily_low:
                self.daily_low = curr_l
                
            sess_name = 'None'
            
            # 1. Asia Session
            if self._is_asia(dt):
                sess_name = 'Asia'
                if curr_h > self.daily_sessions['Asia']['high']:
                    self.daily_sessions['Asia']['high'] = curr_h
                if curr_l < self.daily_sessions['Asia']['low']:
                    self.daily_sessions['Asia']['low'] = curr_l
                    
            # 2. London Killzone
            elif self._is_london(dt):
                sess_name = 'London'
                if curr_h > self.daily_sessions['London']['high']:
                    self.daily_sessions['London']['high'] = curr_h
                if curr_l < self.daily_sessions['London']['low']:
                    self.daily_sessions['London']['low'] = curr_l
                    
                # Check for Asia Session Sweeps
                ah = self.daily_sessions['Asia']['high']
                al = self.daily_sessions['Asia']['low']
                if ah > 0 and curr_h > ah:
                    self.daily_sessions['Asia']['swept_high'] = True
                    if curr_c <= ah:
                        asia_swept_h[i] = True
                if al < float('inf') and curr_l < al:
                    self.daily_sessions['Asia']['swept_low'] = True
                    if curr_c >= al:
                        asia_swept_l[i] = True
                        
            # 3. New York Killzone
            elif self._is_ny(dt):
                sess_name = 'NY'
                if curr_h > self.daily_sessions['NY']['high']:
                    self.daily_sessions['NY']['high'] = curr_h
                if curr_l < self.daily_sessions['NY']['low']:
                    self.daily_sessions['NY']['low'] = curr_l
                    
                # Check for London Sweeps
                lh = self.daily_sessions['London']['high']
                ll = self.daily_sessions['London']['low']
                if lh > 0 and curr_h > lh:
                    self.daily_sessions['London']['swept_high'] = True
                    if curr_c <= lh:
                        london_swept_h[i] = True
                if ll < float('inf') and curr_l < ll:
                    self.daily_sessions['London']['swept_low'] = True
                    if curr_c >= ll:
                        london_swept_l[i] = True
                        
                # Also check Asia Sweeps if still unmitigated
                ah = self.daily_sessions['Asia']['high']
                al = self.daily_sessions['Asia']['low']
                if ah > 0 and curr_h > ah:
                    self.daily_sessions['Asia']['swept_high'] = True
                    if curr_c <= ah:
                        asia_swept_h[i] = True
                if al < float('inf') and curr_l < al:
                    self.daily_sessions['Asia']['swept_low'] = True
                    if curr_c >= al:
                        asia_swept_l[i] = True

            # Record persistent session status
            asia_ever_swept_h[i] = self.daily_sessions['Asia']['swept_high']
            asia_ever_swept_l[i] = self.daily_sessions['Asia']['swept_low']
            london_ever_swept_h[i] = self.daily_sessions['London']['swept_high']
            london_ever_swept_l[i] = self.daily_sessions['London']['swept_low']

            # Check PDH / PDL sweeps
            if self.pdh > 0 and curr_h > self.pdh:
                if curr_c <= self.pdh:
                    pdh_swept_arr[i] = True
            if self.pdl < float('inf') and curr_l < self.pdl:
                if curr_c >= self.pdl:
                    pdl_swept_arr[i] = True
                    
            active_session[i] = sess_name
            asia_high[i] = self.daily_sessions['Asia']['high'] if self.daily_sessions['Asia']['high'] > 0 else np.nan
            asia_low[i] = self.daily_sessions['Asia']['low'] if self.daily_sessions['Asia']['low'] < float('inf') else np.nan
            london_high[i] = self.daily_sessions['London']['high'] if self.daily_sessions['London']['high'] > 0 else np.nan
            london_low[i] = self.daily_sessions['London']['low'] if self.daily_sessions['London']['low'] < float('inf') else np.nan
            ny_high[i] = self.daily_sessions['NY']['high'] if self.daily_sessions['NY']['high'] > 0 else np.nan
            ny_low[i] = self.daily_sessions['NY']['low'] if self.daily_sessions['NY']['low'] < float('inf') else np.nan
            
            pdh_arr[i] = self.pdh if self.pdh > 0 else np.nan
            pdl_arr[i] = self.pdl if self.pdl < float('inf') else np.nan
            
        df['Active_Session'] = active_session
        df['Asia_High'] = asia_high
        df['Asia_Low'] = asia_low
        df['London_High'] = london_high
        df['London_Low'] = london_low
        df['NY_High'] = ny_high
        df['NY_Low'] = ny_low
        df['PDH'] = pdh_arr
        df['PDL'] = pdl_arr
        
        # Immediate active bar sweeps (wick rejection)
        df['Asia_Swept_High'] = asia_swept_h
        df['Asia_Swept_Low'] = asia_swept_l
        df['London_Swept_High'] = london_swept_h
        df['London_Swept_Low'] = london_swept_l
        df['PDH_Swept'] = pdh_swept_arr
        df['PDL_Swept'] = pdl_swept_arr
        
        # Persistent flags throughout session
        df['Asia_Ever_Swept_High'] = asia_ever_swept_h
        df['Asia_Ever_Swept_Low'] = asia_ever_swept_l
        df['London_Ever_Swept_High'] = london_ever_swept_h
        df['London_Ever_Swept_Low'] = london_ever_swept_l
        
        return df
