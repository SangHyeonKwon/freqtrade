# --- 필수 import 구문 ---
import sys
from pathlib import Path
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
# scipy imports는 trendline_lib에서 사용

# user_data 디렉토리를 Python path에 추가하여 lib 모듈 import 가능하게 함
if 'user_data' not in str(sys.path):
    # 현재 파일의 경로에서 user_data 디렉토리 찾기
    current_file = Path(__file__).resolve()
    user_data_dir = current_file.parent.parent  # strategies -> user_data
    if user_data_dir.name == 'user_data' and str(user_data_dir) not in sys.path:
        sys.path.insert(0, str(user_data_dir))

# 캔들 차트 데이터 기반 자동 추세선 라이브러리
from lib.trendline_lib import (
    calculate_support_trendline,
    calculate_resistance_trendline,
    fit_slope_through_point
)

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
    
    # 리스크 관리 (단타 최적화)
    stoploss = -0.015  # 더 타이트한 손절 (-1.5%)
    trailing_stop = True
    trailing_stop_positive = 0.01  # 1% 수익 시 후행 손절 시작
    trailing_stop_positive_offset = 0.015  # 1.5% 수익 시 후행 손절 활성화
    
    # ROI (단타용 - 빠른 실현)
    minimal_roi = {
        "0": 0.01,   # 즉시 1%
        "5": 0.008,  # 5분 후 0.8%
        "10": 0.006, # 10분 후 0.6%
        "15": 0.005, # 15분 후 0.5%
        "30": 0.003  # 30분 후 0.3%
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
        """트렌드라인 기울기 계산 (라이브러리 함수 래퍼)"""
        return fit_slope_through_point(x, y, x0, y0)

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
        
        # MACD (improved 추가) - 딕셔너리 형태로 반환되므로 올바르게 처리
        macd_result = ta.MACD(dataframe)
        dataframe['macd'] = pd.to_numeric(macd_result['macd'], errors='coerce')
        dataframe['macdsignal'] = pd.to_numeric(macd_result['macdsignal'], errors='coerce')
        dataframe['macdhist'] = pd.to_numeric(macd_result['macdhist'], errors='coerce')
        
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
            macd_result_4h = ta.MACD(informative)
            informative['macd'] = pd.to_numeric(macd_result_4h['macd'], errors='coerce')
            informative['macdsignal'] = pd.to_numeric(macd_result_4h['macdsignal'], errors='coerce')
            informative['macdhist'] = pd.to_numeric(macd_result_4h['macdhist'], errors='coerce')

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

        # === 부드러운 추세선 시스템 (라이브러리 사용) ===
        lookback = 150  # 더 긴 기간으로 안정성 확보
        order = 8  # 극값 탐지 민감도 (더 큰 값 = 더 중요한 극값만)
        smooth_window = 30  # 스무딩 윈도우
        
        # 지지선 계산 (라이브러리 함수 사용)
        support_values, support_slope = calculate_support_trendline(
            dataframe, lookback=lookback, order=order, smooth_window=smooth_window
        )
        dataframe['trendline_support_value_pointwise'] = support_values
        dataframe['trendline_support_slope_pointwise'] = support_slope
        
        # 저항선 계산 (라이브러리 함수 사용)
        resistance_values, resistance_slope = calculate_resistance_trendline(
            dataframe, lookback=lookback, order=order, smooth_window=smooth_window
        )
        dataframe['trendline_resistance_value_pointwise'] = resistance_values
        dataframe['trendline_resistance_slope_pointwise'] = resistance_slope
        
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
        # 롱 진입 (단타용 - 조건 완화)
        dataframe.loc[
            (
                # 추세선 조건 완화: 지지선 근처 또는 위
                (dataframe['close'] > dataframe['trendline_support_value_pointwise'] * 0.998) &
                # 기울기는 완화 (0 이상 또는 약간 음수여도 OK)
                (dataframe['trendline_support_slope_pointwise'] > -0.0001) &
                
                # 기술적 지표 완화 (더 넓은 범위)
                (dataframe['rsi_14'] > 35) & (dataframe['rsi_14'] < 75) &
                # MACD 조건 완화 (크로스 직후 또는 상향)
                ((dataframe['macd'] > dataframe['macdsignal']) | (dataframe['macdhist'] > 0)) &
                (dataframe['adx'] > 15) &  # ADX 임계값 낮춤
                (dataframe['volume_ratio'] > 0.8) &  # 볼륨 조건 완화
                
                # BB 조건 완화 (중간선 근처도 OK)
                (dataframe['close'] > dataframe['bb_middle'] * 0.999)
            ),
            ['enter_long', 'enter_tag']
        ] = (1, 'trendline_long')
        
        # 숏 진입 (단타용 - 조건 완화)
        dataframe.loc[
            (
                # 추세선 조건 완화: 저항선 근처 또는 아래
                (dataframe['close'] < dataframe['trendline_resistance_value_pointwise'] * 1.002) &
                # 기울기는 완화 (0 이하 또는 약간 양수여도 OK)
                (dataframe['trendline_resistance_slope_pointwise'] < 0.0001) &
                
                # 기술적 지표 완화 (더 넓은 범위)
                (dataframe['rsi_14'] < 65) & (dataframe['rsi_14'] > 25) &
                # MACD 조건 완화 (크로스 직후 또는 하향)
                ((dataframe['macd'] < dataframe['macdsignal']) | (dataframe['macdhist'] < 0)) &
                (dataframe['adx'] > 15) &  # ADX 임계값 낮춤
                (dataframe['volume_ratio'] > 0.8) &  # 볼륨 조건 완화
                
                # BB 조건 완화 (중간선 근처도 OK)
                (dataframe['close'] < dataframe['bb_middle'] * 1.001)
            ),
            ['enter_short', 'enter_tag']
        ] = (1, 'trendline_short')
        
        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        # 롱 청산 (단타용 - 빠른 실현)
        dataframe.loc[
            (
                # 추세 반전 감지 (더 민감하게)
                (dataframe['trendline_support_slope_pointwise'] < -0.0001) |
                # 과매수 (임계값 낮춤)
                (dataframe['rsi_14'] > 70) |
                # MACD 크로스다운 또는 히스토그램 음전환
                (dataframe['macd'] < dataframe['macdsignal']) |
                (dataframe['macdhist'] < 0) |
                # 가격이 지지선 이탈 (약간의 여유)
                (dataframe['close'] < dataframe['trendline_support_value_pointwise'] * 0.998) |
                # 볼륨 감소 (매수 세력 약화)
                (dataframe['volume_ratio'] < 0.7)
            ),
            ['exit_long', 'exit_tag']
        ] = (1, 'trendline_exit_long')
        
        # 숏 청산 (단타용 - 빠른 실현)
        dataframe.loc[
            (
                # 추세 반전 감지 (더 민감하게)
                (dataframe['trendline_resistance_slope_pointwise'] > 0.0001) |
                # 과매도 (임계값 낮춤)
                (dataframe['rsi_14'] < 30) |
                # MACD 크로스업 또는 히스토그램 양전환
                (dataframe['macd'] > dataframe['macdsignal']) |
                (dataframe['macdhist'] > 0) |
                # 가격이 저항선 돌파 (약간의 여유)
                (dataframe['close'] > dataframe['trendline_resistance_value_pointwise'] * 1.002) |
                # 볼륨 감소 (매도 세력 약화)
                (dataframe['volume_ratio'] < 0.7)
            ),
            ['exit_short', 'exit_tag']
        ] = (1, 'trendline_exit_short')
        
        return dataframe

    def custom_stoploss(self, pair: str, trade: 'Trade', current_time: 'datetime', 
                       current_rate: float, current_profit: float, **kwargs) -> float:
        """단타용 타이트한 동적 스톱로스"""
        dataframe, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
        last_candle = dataframe.iloc[-1].squeeze()
        
        # 추세 반전 시 빠른 손절 (단타용 - 더 민감)
        if not trade.is_short:
            # 롱: 지지선 기울기가 음수로 전환
            if last_candle['trendline_support_slope_pointwise'] < -0.0001:
                return -0.01  # 1% 손절
        else:
            # 숏: 저항선 기울기가 양수로 전환
            if last_candle['trendline_resistance_slope_pointwise'] > 0.0001:
                return -0.01  # 1% 손절
        
        # 기본 ATR 기반 손절 (단타용 - 더 타이트)
        atr_stop_distance = last_candle['atr'] * 1.2  # ATR 배수 감소
        if trade.is_short:
            stop_price = trade.open_rate + atr_stop_distance
            stop_ratio = (stop_price / current_rate) - 1
            # 최대 -1.5% 손절
            return max(stop_ratio, -0.015)
        else:
            stop_price = trade.open_rate - atr_stop_distance  
            stop_ratio = (stop_price / current_rate) - 1
            # 최대 -1.5% 손절
            return max(stop_ratio, -0.015)
