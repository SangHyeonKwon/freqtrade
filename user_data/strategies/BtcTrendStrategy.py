# --- 필수 import 구문 (반드시 포함) ---
import numpy as np
import pandas as pd
from pandas import DataFrame
from freqtrade.strategy import IStrategy, IntParameter, CategoricalParameter, DecimalParameter
import talib.abstract as ta

class BtcTrendStrategy(IStrategy):
    """
    개선된 BTC/USDT 선물 추세추종 전략
    다중 확인 시스템 + 동적 리스크 관리
    """
    
    # 기본 설정
    INTERFACE_VERSION = 3
    timeframe = '15m'
    can_short = True
    startup_candle_count = 200

    # === Hyperopt 파라미터 ===
    # 추세 확인 파라미터
    ema_fast = IntParameter(8, 15, default=12, space="buy")
    ema_slow = IntParameter(20, 35, default=26, space="buy")
    ema_long = IntParameter(80, 120, default=100, space="buy")
    
    # 강도 필터
    adx_min = IntParameter(20, 35, default=25, space="buy")
    rsi_oversold = IntParameter(25, 40, default=30, space="buy")
    rsi_overbought = IntParameter(60, 75, default=70, space="sell")
    
    # 볼륨 필터
    volume_multiplier = DecimalParameter(1.1, 2.0, default=1.5, space="buy")
    
    # ATR 기반 동적 스톱로스
    atr_multiplier = DecimalParameter(1.5, 3.0, default=2.0, space="stoploss")
    
    # ROI 설정
    roi_p1 = DecimalParameter(0.01, 0.03, default=0.015, space="roi")
    roi_p2 = DecimalParameter(0.02, 0.06, default=0.04, space="roi")
    roi_t1 = IntParameter(30, 120, default=60, space="roi")
    roi_t2 = IntParameter(180, 360, default=240, space="roi")

    # 고정 설정
    stoploss = -0.15  # 백업 스탑로스
    trailing_stop = True
    trailing_stop_positive = 0.01
    trailing_stop_positive_offset = 0.02

    def populate_roi(self):
        return {
            "0": self.roi_p2.value,
            str(self.roi_t1.value): self.roi_p1.value,
            str(self.roi_t2.value): 0
        }

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        """지표 계산 - 더 포괄적인 분석"""
        
        # === 이동평균 시스템 ===
        dataframe['ema_fast'] = ta.EMA(dataframe, timeperiod=self.ema_fast.value)
        dataframe['ema_slow'] = ta.EMA(dataframe, timeperiod=self.ema_slow.value)
        dataframe['ema_long'] = ta.EMA(dataframe, timeperiod=self.ema_long.value)
        
        # === 추세 강도 지표 ===
        dataframe['adx'] = ta.ADX(dataframe, timeperiod=14)
        dataframe['di_plus'] = ta.PLUS_DI(dataframe, timeperiod=14)
        dataframe['di_minus'] = ta.MINUS_DI(dataframe, timeperiod=14)
        
        # === 모멘텀 지표 ===
        dataframe['rsi'] = ta.RSI(dataframe, timeperiod=14)
        dataframe['macd'], dataframe['macdsignal'], dataframe['macdhist'] = ta.MACD(dataframe)
        
        # === 변동성 지표 ===
        dataframe['atr'] = ta.ATR(dataframe, timeperiod=14)
        bollinger = ta.BBANDS(dataframe, timeperiod=20, nbdevup=2.0, nbdevdn=2.0)
        dataframe['bb_upper'] = pd.to_numeric(bollinger['upperband'], errors='coerce')
        dataframe['bb_middle'] = pd.to_numeric(bollinger['middleband'], errors='coerce')
        dataframe['bb_lower'] = pd.to_numeric(bollinger['lowerband'], errors='coerce')
        dataframe['bb_width'] = (dataframe['bb_upper'] - dataframe['bb_lower']) / dataframe['bb_middle']
        
        # === 볼륨 분석 ===
        dataframe['volume_sma'] = ta.SMA(dataframe['volume'], timeperiod=20)
        dataframe['volume_ratio'] = dataframe['volume'] / dataframe['volume_sma']
        
        # === 매도세/매수세 지표 추가 ===
        # 1. VWAP 기반 매도세/매수세
        dataframe['vwap'] = (dataframe['volume'] * (dataframe['high'] + dataframe['low'] + dataframe['close']) / 3).cumsum() / dataframe['volume'].cumsum()
        dataframe['price_vs_vwap'] = (dataframe['close'] - dataframe['vwap']) / dataframe['vwap'] * 100
        
        # 2. 대형 거래 감지
        dataframe['volume_spike'] = dataframe['volume'] / dataframe['volume_sma']
        dataframe['price_change_pct'] = dataframe['close'].pct_change() * 100
        
        # 3. 매도세/매수세 점수 계산
        dataframe['buying_pressure'] = np.where(
            (dataframe['price_change_pct'] > 0) & 
            (dataframe['volume_spike'] > 1.5) & 
            (dataframe['price_vs_vwap'] > 0),
            dataframe['price_change_pct'] * dataframe['volume_spike'],
            0
        )
        
        dataframe['selling_pressure'] = np.where(
            (dataframe['price_change_pct'] < 0) & 
            (dataframe['volume_spike'] > 1.5) & 
            (dataframe['price_vs_vwap'] < 0),
            abs(dataframe['price_change_pct']) * dataframe['volume_spike'],
            0
        )
        
        # 4. 누적 매도세/매수세
        dataframe['cumulative_buying_pressure'] = dataframe['buying_pressure'].rolling(window=10).sum()
        dataframe['cumulative_selling_pressure'] = dataframe['selling_pressure'].rolling(window=10).sum()
        
        # 5. 매도세/매수세 균형 지표
        dataframe['pressure_balance'] = dataframe['cumulative_buying_pressure'] - dataframe['cumulative_selling_pressure']
        dataframe['pressure_ratio'] = dataframe['cumulative_buying_pressure'] / (dataframe['cumulative_selling_pressure'] + 1e-8)
        
        # 6. 대형 투자자 행동 패턴 감지
        dataframe['whale_activity'] = np.where(
            (dataframe['volume_spike'] > 3.0) & 
            (abs(dataframe['price_change_pct']) > 2.0),
            1, 0
        )
        
        # 7. 매도세/매수세 신호
        dataframe['strong_buying_signal'] = np.where(
            (dataframe['pressure_balance'] > dataframe['pressure_balance'].rolling(window=20).quantile(0.8)) &
            (dataframe['buying_pressure'].rolling(window=5).mean() > dataframe['selling_pressure'].rolling(window=5).mean() * 2),
            1, 0
        )
        
        dataframe['strong_selling_signal'] = np.where(
            (dataframe['pressure_balance'] < dataframe['pressure_balance'].rolling(window=20).quantile(0.2)) &
            (dataframe['selling_pressure'].rolling(window=5).mean() > dataframe['buying_pressure'].rolling(window=5).mean() * 2),
            1, 0
        )
        
        # === 추가 필터 ===
        # 가격 위치 (볼린저 밴드 내 위치)
        dataframe['bb_position'] = (dataframe['close'] - dataframe['bb_lower']) / (dataframe['bb_upper'] - dataframe['bb_lower'])
        
        # 추세 일관성 체크
        dataframe['trend_consistency'] = (
            (dataframe['ema_fast'] > dataframe['ema_slow']).rolling(window=3).sum()
        )
        
        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        """개선된 진입 신호 - 다중 확인 시스템 + 매도세/매수세"""
        
        # === 롱 진입 조건 ===
        long_conditions = [
            # 기본 추세 확인
            (dataframe['ema_fast'] > dataframe['ema_slow']),
            (dataframe['ema_slow'] > dataframe['ema_long']),
            (dataframe['close'] > dataframe['ema_fast']),
            
            # 추세 강도 확인
            (dataframe['adx'] > self.adx_min.value),
            (dataframe['di_plus'] > dataframe['di_minus']),
            
            # 모멘텀 확인
            (dataframe['rsi'] > self.rsi_oversold.value),
            (dataframe['rsi'] < 65),  # 과매수 회피
            (dataframe['macd'] > dataframe['macdsignal']),
            
            # 변동성 및 볼륨 확인
            (dataframe['volume_ratio'] > self.volume_multiplier.value),
            (dataframe['bb_width'] > 0.02),  # 충분한 변동성
            
            # 추세 일관성 확인
            (dataframe['trend_consistency'] >= 2),
            
            # 매수세 강화 조건 추가
            ((dataframe['pressure_balance'] > 0) | (dataframe['strong_buying_signal'] == 1)),
            (dataframe['pressure_ratio'] > 1.2)  # 매수세가 매도세보다 20% 이상 강함
        ]
        
        dataframe.loc[
            reduce(lambda x, y: x & y, long_conditions),
            ['enter_long', 'enter_tag']
        ] = (1, 'advanced_long_with_buying_pressure')
        
        # === 숏 진입 조건 ===
        short_conditions = [
            # 기본 추세 확인
            (dataframe['ema_fast'] < dataframe['ema_slow']),
            (dataframe['ema_slow'] < dataframe['ema_long']),
            (dataframe['close'] < dataframe['ema_fast']),
            
            # 추세 강도 확인
            (dataframe['adx'] > self.adx_min.value),
            (dataframe['di_minus'] > dataframe['di_plus']),
            
            # 모멘텀 확인
            (dataframe['rsi'] < self.rsi_overbought.value),
            (dataframe['rsi'] > 35),  # 과매도 회피
            (dataframe['macd'] < dataframe['macdsignal']),
            
            # 변동성 및 볼륨 확인
            (dataframe['volume_ratio'] > self.volume_multiplier.value),
            (dataframe['bb_width'] > 0.02),
            
            # 추세 일관성 확인 (반대)
            (dataframe['trend_consistency'] <= 1),
            
            # 매도세 강화 조건 추가
            ((dataframe['pressure_balance'] < 0) | (dataframe['strong_selling_signal'] == 1)),
            (dataframe['pressure_ratio'] < 0.8)  # 매도세가 매수세보다 20% 이상 강함
        ]
        
        dataframe.loc[
            reduce(lambda x, y: x & y, short_conditions),
            ['enter_short', 'enter_tag']
        ] = (1, 'advanced_short_with_selling_pressure')
        
        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        """개선된 청산 신호 + 매도세/매수세"""
        
        # === 롱 청산 조건 ===
        dataframe.loc[
            (
                # 추세 반전 신호
                (dataframe['ema_fast'] <= dataframe['ema_slow']) |
                # 추세 약화
                (dataframe['adx'] < 20) |
                # RSI 과매수
                (dataframe['rsi'] > 75) |
                # MACD 다이버전스
                (dataframe['macd'] <= dataframe['macdsignal']) |
                # 볼린저 밴드 상한선 근처
                (dataframe['bb_position'] > 0.9) |
                # 매도세 강화 또는 매수세 약화
                (dataframe['strong_selling_signal'] == 1) |
                (dataframe['pressure_ratio'] < 0.5)  # 매수세가 매도세의 절반 이하로 약화
            ),
            ['exit_long', 'exit_tag']
        ] = (1, 'advanced_exit_with_pressure')
        
        # === 숏 청산 조건 ===
        dataframe.loc[
            (
                # 추세 반전 신호
                (dataframe['ema_fast'] >= dataframe['ema_slow']) |
                # 추세 약화
                (dataframe['adx'] < 20) |
                # RSI 과매도
                (dataframe['rsi'] < 25) |
                # MACD 다이버전스
                (dataframe['macd'] >= dataframe['macdsignal']) |
                # 볼린저 밴드 하한선 근처
                (dataframe['bb_position'] < 0.1) |
                # 매수세 강화 또는 매도세 약화
                (dataframe['strong_buying_signal'] == 1) |
                (dataframe['pressure_ratio'] > 2.0)  # 매수세가 매도세의 2배 이상으로 강화
            ),
            ['exit_short', 'exit_tag']
        ] = (1, 'advanced_exit_with_pressure')
        
        return dataframe

    def custom_stoploss(self, pair: str, trade: 'Trade', current_time: 'datetime', 
                       current_rate: float, current_profit: float, **kwargs) -> float:
        """ATR 기반 동적 스톱로스"""
        
        dataframe, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
        last_candle = dataframe.iloc[-1]
        
        # ATR 기반 스톱로스 계산
        atr_stop_distance = last_candle['atr'] * self.atr_multiplier.value
        
        if trade.is_short:
            stop_price = trade.open_rate + atr_stop_distance
            return (stop_price / current_rate) - 1
        else:
            stop_price = trade.open_rate - atr_stop_distance  
            return (stop_price / current_rate) - 1

# 필요한 import 추가
from functools import reduce
