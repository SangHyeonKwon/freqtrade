# --- 필수 import 구문 ---
import numpy as np
import pandas as pd
import logging
from pandas import DataFrame
from freqtrade.strategy import (
    IStrategy,
    IntParameter,
    CategoricalParameter,
    DecimalParameter,
    merge_informative_pair,
)
import talib.abstract as ta
from functools import reduce
from scipy.signal import argrelextrema
from scipy.cluster.vq import kmeans

logger = logging.getLogger(__name__)

class TrendPro(IStrategy):
    """
    최적 결합 전략: 옛 전략의 트렌드라인 + 새 전략의 개선사항
    """
    
    # 기본 설정 (옛 전략 기반)
    INTERFACE_VERSION = 3
    timeframe = '15m'
    informative_timeframe = '4h'
    can_short = True
    startup_candle_count = 320
    
    # 리스크 관리 (improved 버전 적용)
    stoploss = -0.04
    trailing_stop = True
    trailing_stop_positive = 0.02
    trailing_stop_positive_offset = 0.025
    
    # ROI (옛 전략 방식)
    minimal_roi = {
        "0": 0.04,
        "15": 0.06,
        "30": 0.08,
        "60": 0.09,
        "120": 0.10 
    }

    # === Hyperopt 파라미터 (improved 버전) ===
    # RSI 파라미터 (옛 전략 방식)
    buy_rsi = IntParameter(10, 40, default=30, space="buy")
    sell_rsi = IntParameter(60, 90, default=70, space="sell")
    
    # 트렌드라인 기울기 임계값 (새로 추가)
    trendline_slope_threshold = DecimalParameter(0.2, 0.6, default=0.3, space="buy")
    support_slope_threshold = DecimalParameter(0.2, 0.6, default=0.3, space="buy")
    resistance_slope_threshold = DecimalParameter(0.2, 0.6, default=0.4, space="sell")
    
    # 볼륨 필터 (improved 추가)
    volume_multiplier = DecimalParameter(1.1, 2.0, default=1.2, space="buy")

    # === 차트 시각화 설정 (핵심 추세선만 표시) ===
    plot_config = {
        'main_plot': {
            # 핵심 추세선 2개만 표시 (명확하게 구분)
            'trendline_support_value_pointwise': {
                'color': '#00ff00',  # 밝은 초록색 (지지선)
                'type': 'scatter',
                'plotly': {
                    'mode': 'lines',
                    'line': {'width': 3},  # 더 굵게
                    'name': '지지선'
                }
            },
            'trendline_resistance_value_pointwise': {
                'color': '#ff0000',  # 밝은 빨간색 (저항선)
                'type': 'scatter',
                'plotly': {
                    'mode': 'lines',
                    'line': {'width': 3},  # 더 굵게
                    'name': '저항선'
                }
            },
            # 볼린저 밴드는 제거 (추세선만 보이게)
        },
        'subplots': {
            # RSI 서브플롯
            "RSI": {
                'rsi_14': {'color': '#ff9800'},
            },
            # MACD 서브플롯
            "MACD": {
                'macd': {'color': '#2196f3'},
                'macdsignal': {'color': '#ff5722'},
                'macdhist': {
                    'color': '#26a69a',
                    'type': 'bar',
                    'plotly': {'opacity': 0.5}
                }
            },
            # ADX (추세 강도)
            "ADX": {
                'adx': {'color': '#9c27b0'}
            }
        }
    }

    def informative_pairs(self):
        """15m + 4h 다중 타임프레임 구성"""
        pairs = self.dp.current_whitelist() if self.dp else []
        informative_pairs = [(pair, self.informative_timeframe) for pair in pairs]
        return informative_pairs

    def past_non_nan_avg(self, series: DataFrame, window=3):
        """옛 전략의 안정적 평균 계산 함수"""
        result = []
        buffer = []
        current_avg = np.nan
        
        for i in range(len(series)):
            val = series.iloc[i]
            if not pd.isna(val):
                buffer.append(val)
                if len(buffer) > window:
                    buffer.pop(0)
                if len(buffer) == window:
                    current_avg = sum(buffer) / window
                result.append(current_avg)
            else:
                result.append(current_avg)
        return pd.Series(result, index=series.index)

    def fit_slope_through_point(self, x, y, x0, y0):
        """옛 전략의 트렌드라인 기울기 계산"""
        x_shifted = x - x0
        y_shifted = y - y0
        weights = np.linspace(1, 4, len(x))
        numerator = np.sum(weights * x_shifted * y_shifted)
        denominator = np.sum(weights * x_shifted ** 2)
        if denominator == 0:
            return 0
        return numerator / denominator

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        # === 기본 지표들 (improved 버전) ===
        dataframe['rsi_14'] = ta.RSI(dataframe['close'], timeperiod=14)
        dataframe['adx'] = ta.ADX(dataframe, timeperiod=14)
        dataframe['atr'] = ta.ATR(dataframe, timeperiod=14)
        
        # 볼린저 밴드 (improved 안전 처리)
        bollinger = ta.BBANDS(dataframe, timeperiod=20)
        dataframe['bb_upper'] = pd.to_numeric(bollinger['upperband'], errors='coerce')
        dataframe['bb_middle'] = pd.to_numeric(bollinger['middleband'], errors='coerce')
        dataframe['bb_lower'] = pd.to_numeric(bollinger['lowerband'], errors='coerce')
        
        # MACD (improved 추가)
        dataframe['macd'], dataframe['macdsignal'], dataframe['macdhist'] = ta.MACD(dataframe)
        
        # 볼륨 분석 (improved)
        dataframe['vol_sma'] = dataframe['volume'].rolling(window=20).mean()
        dataframe['volume_ratio'] = dataframe['volume'] / dataframe['vol_sma']
        # === 4h 인포매티브 지표 ===
        if self.dp:
            informative = self.dp.get_pair_dataframe(
                pair=metadata['pair'],
                timeframe=self.informative_timeframe
            ).copy()

            informative['rsi_14'] = ta.RSI(informative['close'], timeperiod=14)
            informative['adx'] = ta.ADX(informative, timeperiod=14)
            informative['macd'], informative['macdsignal'], informative['macdhist'] = ta.MACD(informative)

            dataframe = merge_informative_pair(
                dataframe,
                informative,
                self.timeframe,
                self.informative_timeframe,
                ffill=True
            )
        for col in ['rsi_14_4h', 'macd_4h', 'macdsignal_4h', 'macdhist_4h', 'adx_4h']:
            if col not in dataframe.columns:
                dataframe[col] = np.nan

        # === 부드러운 추세선 시스템 (스무딩 적용) ===
        lookback = 150  # 더 긴 기간으로 안정성 확보
        order = 8  # 극값 탐지 민감도 (더 큰 값 = 더 중요한 극값만)
        smooth_window = 30  # 스무딩 윈도우
        
        # 지지선 계산: 전체 기간에서 주요 저점 찾기
        if len(dataframe) >= lookback:
            # 전체 기간의 저점 찾기 (더 안정적)
            lows = dataframe['low'].values
            extrema_idx = argrelextrema(lows, np.less_equal, order=order)[0]
            
            if len(extrema_idx) >= 3:
                # 최근 극값들만 사용 (너무 오래된 건 제외)
                recent_extrema = extrema_idx[extrema_idx >= len(dataframe) - lookback]
                if len(recent_extrema) >= 2:
                    x = recent_extrema
                    y = lows[recent_extrema]
                    
                    # 선형회귀로 추세선 계산
                    coeffs = np.polyfit(x, y, 1)
                    slope_val = coeffs[0]
                    intercept_val = coeffs[1]
                    
                    # 전체 기간에 추세선 값 계산
                    support_line = slope_val * np.arange(len(dataframe)) + intercept_val
                else:
                    # 극값이 부족하면 EMA 기반
                    support_line = dataframe['low'].ewm(span=lookback, adjust=False).mean().values
                    slope_val = (support_line[-1] - support_line[-lookback]) / lookback if len(support_line) > lookback else 0
            else:
                # 극값이 부족하면 EMA 기반
                support_line = dataframe['low'].ewm(span=lookback, adjust=False).mean().values
                slope_val = (support_line[-1] - support_line[-lookback]) / lookback if len(support_line) > lookback else 0
        else:
            # 데이터가 부족하면 EMA 기반
            support_line = dataframe['low'].ewm(span=min(lookback, len(dataframe)), adjust=False).mean().values
            slope_val = 0
        
        # 스무딩 적용 (부드러운 추세선)
        support_series = pd.Series(support_line, index=dataframe.index)
        dataframe['trendline_support_value_pointwise'] = support_series.rolling(window=smooth_window, center=True).mean().bfill().ffill()
        dataframe['trendline_support_slope_pointwise'] = slope_val
        
        # 저항선 계산: 전체 기간에서 주요 고점 찾기
        if len(dataframe) >= lookback:
            # 전체 기간의 고점 찾기
            highs = dataframe['high'].values
            extrema_idx = argrelextrema(highs, np.greater_equal, order=order)[0]
            
            if len(extrema_idx) >= 3:
                # 최근 극값들만 사용
                recent_extrema = extrema_idx[extrema_idx >= len(dataframe) - lookback]
                if len(recent_extrema) >= 2:
                    x = recent_extrema
                    y = highs[recent_extrema]
                    
                    # 선형회귀로 추세선 계산
                    coeffs = np.polyfit(x, y, 1)
                    slope_val = coeffs[0]
                    intercept_val = coeffs[1]
                    
                    # 전체 기간에 추세선 값 계산
                    resistance_line = slope_val * np.arange(len(dataframe)) + intercept_val
                else:
                    # 극값이 부족하면 EMA 기반
                    resistance_line = dataframe['high'].ewm(span=lookback, adjust=False).mean().values
                    slope_val = (resistance_line[-1] - resistance_line[-lookback]) / lookback if len(resistance_line) > lookback else 0
            else:
                # 극값이 부족하면 EMA 기반
                resistance_line = dataframe['high'].ewm(span=lookback, adjust=False).mean().values
                slope_val = (resistance_line[-1] - resistance_line[-lookback]) / lookback if len(resistance_line) > lookback else 0
        else:
            # 데이터가 부족하면 EMA 기반
            resistance_line = dataframe['high'].ewm(span=min(lookback, len(dataframe)), adjust=False).mean().values
            slope_val = 0
        
        # 스무딩 적용 (부드러운 추세선)
        resistance_series = pd.Series(resistance_line, index=dataframe.index)
        dataframe['trendline_resistance_value_pointwise'] = resistance_series.rolling(window=smooth_window, center=True).mean().bfill().ffill()
        dataframe['trendline_resistance_slope_pointwise'] = slope_val
        
        # 호환성을 위한 더미 컬럼
        dataframe['trendline_support_group_last_value'] = dataframe['trendline_support_value_pointwise']
        dataframe['trendline_resistance_group_last_value'] = dataframe['trendline_resistance_value_pointwise']
        
        # [디버깅] 데이터 확인 로그 (Live/Dry 모드에서만 출력)
        if self.dp and self.dp.runmode.value in ('live', 'dry_run'):
            try:
                last_candle = dataframe.iloc[-1]
                logger.info(f"[{metadata['pair']}] Trendline Check:")
                logger.info(f"  Sup: {last_candle.get('trendline_support_value_pointwise')}")
                logger.info(f"  Res: {last_candle.get('trendline_resistance_value_pointwise')}")
            except Exception:
                pass
        
        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        # 롱 진입 (단순화된 추세선 기반)
        dataframe.loc[
            (
                # 추세선 조건: 가격이 지지선 위, 상승 추세
                (dataframe['close'] > dataframe['trendline_support_value_pointwise']) &
                (dataframe['trendline_support_slope_pointwise'] > 0) &
                
                # 기술적 지표
                (dataframe['rsi_14'] > 40) & (dataframe['rsi_14'] < 70) &
                (dataframe['macd'] > dataframe['macdsignal']) &
                (dataframe['adx'] > 20) &
                (dataframe['volume_ratio'] > self.volume_multiplier.value) &
                
                # 가격이 BB 중간선 위
                (dataframe['close'] > dataframe['bb_middle'])
            ),
            ['enter_long', 'enter_tag']
        ] = (1, 'trendline_long')
        
        # 숏 진입 (단순화된 추세선 기반)
        dataframe.loc[
            (
                # 추세선 조건: 가격이 저항선 아래, 하락 추세
                (dataframe['close'] < dataframe['trendline_resistance_value_pointwise']) &
                (dataframe['trendline_resistance_slope_pointwise'] < 0) &
                
                # 기술적 지표
                (dataframe['rsi_14'] < 60) & (dataframe['rsi_14'] > 30) &
                (dataframe['macd'] < dataframe['macdsignal']) &
                (dataframe['adx'] > 20) &
                (dataframe['volume_ratio'] > self.volume_multiplier.value) &
                
                # 가격이 BB 중간선 아래
                (dataframe['close'] < dataframe['bb_middle'])
            ),
            ['enter_short', 'enter_tag']
        ] = (1, 'trendline_short')
        
        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        # 롱 청산 (단순화)
        dataframe.loc[
            (
                # 추세 반전: 지지선 기울기가 음수로 전환
                (dataframe['trendline_support_slope_pointwise'] < 0) |
                # 과매수
                (dataframe['rsi_14'] > 75) |
                # MACD 크로스다운
                (dataframe['macd'] < dataframe['macdsignal']) |
                # 가격이 지지선 이탈
                (dataframe['close'] < dataframe['trendline_support_value_pointwise'])
            ),
            ['exit_long', 'exit_tag']
        ] = (1, 'trendline_exit_long')
        
        # 숏 청산 (단순화)
        dataframe.loc[
            (
                # 추세 반전: 저항선 기울기가 양수로 전환
                (dataframe['trendline_resistance_slope_pointwise'] > 0) |
                # 과매도
                (dataframe['rsi_14'] < 25) |
                # MACD 크로스업
                (dataframe['macd'] > dataframe['macdsignal']) |
                # 가격이 저항선 돌파
                (dataframe['close'] > dataframe['trendline_resistance_value_pointwise'])
            ),
            ['exit_short', 'exit_tag']
        ] = (1, 'trendline_exit_short')
        
        return dataframe

    def custom_stoploss(self, pair: str, trade: 'Trade', current_time: 'datetime', 
                       current_rate: float, current_profit: float, **kwargs) -> float:
        """단순화된 ATR 기반 동적 스톱로스"""
        dataframe, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
        last_candle = dataframe.iloc[-1].squeeze()
        
        # 추세 반전 시 빠른 손절
        if not trade.is_short:
            # 롱: 지지선 기울기가 음수로 전환
            if last_candle['trendline_support_slope_pointwise'] < -0.5:
                return -0.03
        else:
            # 숏: 저항선 기울기가 양수로 전환
            if last_candle['trendline_resistance_slope_pointwise'] > 0.5:
                return -0.03
        
        # 기본 ATR 기반 손절
        atr_stop_distance = last_candle['atr'] * 2.0
        if trade.is_short:
            stop_price = trade.open_rate + atr_stop_distance
            return (stop_price / current_rate) - 1
        else:
            stop_price = trade.open_rate - atr_stop_distance  
            return (stop_price / current_rate) - 1
