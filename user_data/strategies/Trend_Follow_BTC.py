# pragma pylint: disable=missing-docstring, invalid-name, pointless-string-statement
# flake8: noqa: F401
# isort: skip_file
# --- Do not remove these imports ---
import numpy as np
import pandas as pd
from datetime import datetime, timedelta, timezone
from pandas import DataFrame
from typing import Dict, Optional, Union, Tuple

from freqtrade.strategy import (
    IStrategy,
    Trade,
    Order,
    PairLocks,
    informative,  # @informative decorator
    # Hyperopt Parameters
    BooleanParameter,
    CategoricalParameter,
    DecimalParameter,
    IntParameter,
    RealParameter,
    # timeframe helpers
    timeframe_to_minutes,
    timeframe_to_next_date,
    timeframe_to_prev_date,
    # Strategy helper functions
    merge_informative_pair,
    stoploss_from_absolute,
    stoploss_from_open,
)

# --------------------------------
# Add your lib to import here
import talib.abstract as ta
import pandas_ta as pta
from technical import qtpylib
from scipy.signal import argrelextrema
from scipy.signal import find_peaks
from scipy.cluster.vq import kmeans


class Trend_Follow_BTC(IStrategy):
    """
    This is a strategy template to get you started.
    More information in https://www.freqtrade.io/en/latest/strategy-customization/

    You can:
        :return: a Dataframe with all mandatory indicators for the strategies
    - Rename the class name (Do not forget to update class_name)
    - Add any methods you want to build your strategy
    - Add any lib you need to build your strategy

    You must keep:
    - the lib in the section "Do not remove these libs"
    - the methods: populate_indicators, populate_entry_trend, populate_exit_trend
    You should keep:
    - timeframe, minimal_roi, stoploss, trailing_*
    """
    # Strategy interface version - allow new iterations of the strategy interface.
    # Check the documentation or the Sample strategy to get the latest version.
    INTERFACE_VERSION = 3

    # Optimal timeframe for the strategy.
    timeframe = "15m" # 15분 봉

    # Can this strategy go short?
    # can_short: bool = True
    # 숏 포 허용
    can_short: bool = True

    # Minimal ROI designed for the strategy.
    # This attribute will be overridden if the config file contains "minimal_roi".
    # 최소 수익률 설정, 익절 설정 가능한 기준치
    # 시간지남에 따라 증가하는 방식을 이용 (백테스트에 좋을듯)
    minimal_roi = {
        "0": 0.04,
        "15": 0.06,
        "30" : 0.08,
        "60": 0.09,
        "120": 0.10 
    }

    # Optimal stoploss designed for the strategy.
    # This attribute will be overridden if the config file contains "stoploss".
    
    # 손절 범위
    stoploss = -0.04

    # trailing_stop = False  
    # trailing_stop_positive = 0.01
    # trailing_stop_positive_offset = 0.05
    # trailing_only_offset_is_reached = False

    # trailing_only_offset_is_reached = False
    # trailing_stop_positive = 0.01
    # trailing_stop_positive_offset = 0.0  # Disabled / not configured

    # Run "populate_indicators()" only for new candle.
    # 새 캔들에 대해서만 로직 실행
    process_only_new_candles = True

    # 커스텀 익절/손절 로직 사용
    use_custom_exit = True
    use_custom_stoploss = True

    # These values can be overridden in the config.
    # 탈출 조건이 있으면 적용
    # 수익일때만 청산 X (손절도 허용)
    # 진입 조건 만족 시 ROI 무시 X
    use_exit_signal = True
    exit_profit_only = False
    ignore_roi_if_entry_signal = False

    # Number of candles the strategy requires before producing valid signals
    # 최소 캔들 수 (지표 계산용)
    startup_candle_count: int = 10

    # Strategy parameters
    # RSI : 상대강도지수
    # RSI 전략 파라미터
    buy_rsi = IntParameter(10, 40, default=30, space="buy")
    sell_rsi = IntParameter(60, 90, default=70, space="sell")# Optional order type mapping.
    order_types = {
        "entry": "limit",
        "exit": "limit",
        "stoploss": "market",
        "stoploss_on_exchange": False
    }

    # Optional order time in force.
    order_time_in_force = {
        # GTC : Good Till Cancled
        # 내가 미체결 주문 직접 취소 가능
        "entry": "GTC",
        "exit": "GTC"
    }

    # 전략 그래프 Freq UI에 출력
    @property
    def plot_config(self):
        return {
            # Main plot indicators (Moving averages, ...)
            "main_plot": {
                "trendline_support_group": {"color": "red"},  # 지지선 (파란색)
                "trendline_resistance_group": {"color": "blue"},  # 저항선 (빨간색)
                "trendline": {"color": "orange"},  # 저항선 (빨간색, 끊긴 선)
                "vwap": {"color": "purple"},  # VWAP
                
                # "bb_upper": {"color": "magenta"},
                # "bb_lower": {"color": "teal"},
                # "bb_middle": {"color": "mediumseagreen"},
                # "ma_20": {"color": "green"},
                # "ma_50": {"color": "skyblue"}
            },
            "subplots": {
                # Subplots - each dict defines one additional plot
                "MACD": {
                    "macd": {"color": "blue"},
                    "macd_signal": {"color": "orange"},
                },
                "RSI": {
                    "rsi_14": {"color": "red"},
                },
                "SLPE": {
                    "trendline_support_slope_pointwise": {"color": "coral"},
                    "trendline_resistance_slope_pointwise": {"color": "navy"},
                    "trendline_slope_pointwise": {"color": "green"},  # 지지선 (파란색, 끊긴 선)
                },
                "SL": {
                    "trendline_support_group_last_value": {"color": "salmon"},
                    "trendline_resistance_group_last_value": {"color": "turquoise"},
                },
                "매도세/매수세": {
                    "buying_pressure": {"color": "green", "type": "bar"},
                    "selling_pressure": {"color": "red", "type": "bar"},
                    "pressure_balance": {"color": "blue"},
                    "pressure_ratio": {"color": "orange"},
                },
                "거래량": {
                    "volume": {"color": "gray", "type": "bar"},
                    "volume_spike": {"color": "yellow"},
                    "whale_activity": {"color": "red", "type": "bar"},
                },
                "VWAP": {
                    "price_vs_vwap": {"color": "purple"},
                }
            }
        }


    # 추가로 불러올 페어
    def informative_pairs(self):
        """
        Define additional, informative pair/interval combinations to be cached from the exchange.
        These pair/interval combinations are non-tradeable, unless they are part
        of the whitelist as well.
        For more information, please consult the documentation
        :return: List of tuples in the format (pair, interval)
            Sample: return [("ETH/USDT", "5m"),
                            ("BTC/USDT", "15m"),
                            ]
        """
        return []


    # 불안정한 데이터들 환경에서도 안정적으로 평균을 유지한다
    def past_non_nan_avg(self, series:DataFrame, window=3):
        """
        NaN이 아닌 값들만으로 rolling 평균을 계산하여 NaN을 회피함.
        최근 window 개수만 유지하며, 평균값이 존재하지 않을 경우 이전 평균을 유지함.
        """
        result = []
        buffer = []
        current_avg = np.nan

        for i in range(len(series)):
            val = series.iloc[i]

            if not pd.isna(val):
                buffer.append(val)

                # 최신 window개만 유지
                if len(buffer) > window:
                    buffer.pop(0)

                # window 개수 채워졌으면 새 평균 계산
                if len(buffer) == window:
                    current_avg = sum(buffer) / window

                result.append(current_avg)  # 현재 행에도 평균 AS넣음

            else:
                result.append(current_avg)  # 이전 평균 유지해서 넣음

        return pd.Series(result, index=series.index)

    def fit_slope_through_point(self, x, y, x0, y0):
        # x, y: 전체 피팅할 점들
        # x0, y0: 반드시 통과할 고정점

        # 상대 좌표로 변환
        x_shifted = x - x0
        y_shifted = y - y0

        # 시간순으로 가중치 증가 (예: 1 ~ 3 사이)
        weights = np.linspace(1, 4, len(x))  # 뒤로 갈수록 3배 가중치

        numerator = np.sum(weights * x_shifted * y_shifted)
        denominator = np.sum(weights * x_shifted ** 2)

        if denominator == 0:
            return 0

        slope = numerator / denominator
        return slope
    
    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        # 볼린저 밴드 (기간 20)
        dataframe['bb_upper'], dataframe['bb_middle'], dataframe['bb_lower'] = ta.BBANDS(dataframe['close'], timeperiod=20, nbdevup=2.0, nbdevdn=2.0, matype=0)
        dataframe['bb_width'] = dataframe['bb_upper'] - dataframe['bb_lower']
        dataframe['bb_threshold'] = dataframe['close'] * 0.01  # 예: 1% 이상 폭만 허용

        # 볼린저 밴드 Z값 계산 (현재 종가가 밴드 내에서 위치하는 정도)
        dataframe['bb_z'] = (dataframe['close'] - dataframe['bb_middle']) / (dataframe['bb_upper'] - dataframe['bb_lower'])

        # RSI (2)
        dataframe['rsi_14'] = ta.RSI(dataframe['close'], timeperiod=14)

        
        dataframe['ma_20'] = ta.EMA(dataframe, timeperiod=20)
        dataframe['ma_50'] = ta.EMA(dataframe, timeperiod=50)
        dataframe['golden_cross'] = np.where(dataframe['ma_20'] > dataframe['ma_50'], 1, 0)
        dataframe['dead_cross'] = np.where(dataframe['ma_20'] < dataframe['ma_50'], 1, 0)
        dataframe['macd'], dataframe['macd_signal'], dataframe['macd_hist'] = ta.MACD(dataframe['close'], fastperiod=12, slowperiod=26, signalperiod=9)

        dataframe['bb_position'] = (dataframe['close'] - dataframe['bb_lower']) / (dataframe['bb_upper'] - dataframe['bb_lower'])
        dataframe['vol_sma'] = dataframe['volume'].rolling(window=20).mean()

        # ===== 매도세/매수세 지표 추가 =====
        # 1. 거래량 가중 평균가격 (VWAP) 기반 매도세/매수세
        dataframe['vwap'] = (dataframe['volume'] * (dataframe['high'] + dataframe['low'] + dataframe['close']) / 3).cumsum() / dataframe['volume'].cumsum()
        dataframe['price_vs_vwap'] = (dataframe['close'] - dataframe['vwap']) / dataframe['vwap'] * 100
        
        # 2. 대형 거래 감지 (거래량 급증 + 가격 변화)
        dataframe['volume_ma_20'] = dataframe['volume'].rolling(window=20).mean()
        dataframe['volume_spike'] = dataframe['volume'] / dataframe['volume_ma_20']
        dataframe['price_change_pct'] = dataframe['close'].pct_change() * 100
        
        # 3. 매도세/매수세 점수 계산
        # 매수세: 가격 상승 + 거래량 증가 + VWAP 위에서 거래
        dataframe['buying_pressure'] = np.where(
            (dataframe['price_change_pct'] > 0) & 
            (dataframe['volume_spike'] > 1.5) & 
            (dataframe['price_vs_vwap'] > 0),
            dataframe['price_change_pct'] * dataframe['volume_spike'],
            0
        )
        
        # 매도세: 가격 하락 + 거래량 증가 + VWAP 아래에서 거래
        dataframe['selling_pressure'] = np.where(
            (dataframe['price_change_pct'] < 0) & 
            (dataframe['volume_spike'] > 1.5) & 
            (dataframe['price_vs_vwap'] < 0),
            abs(dataframe['price_change_pct']) * dataframe['volume_spike'],
            0
        )
        
        # 4. 누적 매도세/매수세 (최근 10개 캔들)
        dataframe['cumulative_buying_pressure'] = dataframe['buying_pressure'].rolling(window=10).sum()
        dataframe['cumulative_selling_pressure'] = dataframe['selling_pressure'].rolling(window=10).sum()
        
        # 5. 매도세/매수세 균형 지표
        dataframe['pressure_balance'] = dataframe['cumulative_buying_pressure'] - dataframe['cumulative_selling_pressure']
        dataframe['pressure_ratio'] = dataframe['cumulative_buying_pressure'] / (dataframe['cumulative_selling_pressure'] + 1e-8)
        
        # 6. 대형 투자자 행동 패턴 감지
        # 거래량이 평균의 3배 이상이면서 가격이 크게 움직인 경우
        dataframe['whale_activity'] = np.where(
            (dataframe['volume_spike'] > 3.0) & 
            (abs(dataframe['price_change_pct']) > 2.0),
            1, 0
        )
        
        # 7. 매도세/매수세 추세 (이동평균)
        dataframe['buying_pressure_ma'] = dataframe['buying_pressure'].rolling(window=5).mean()
        dataframe['selling_pressure_ma'] = dataframe['selling_pressure'].rolling(window=5).mean()
        
        # 8. 매도세/매수세 신호
        dataframe['strong_buying_signal'] = np.where(
            (dataframe['pressure_balance'] > dataframe['pressure_balance'].rolling(window=20).quantile(0.8)) &
            (dataframe['buying_pressure_ma'] > dataframe['selling_pressure_ma'] * 2),
            1, 0
        )
        
        dataframe['strong_selling_signal'] = np.where(
            (dataframe['pressure_balance'] < dataframe['pressure_balance'].rolling(window=20).quantile(0.2)) &
            (dataframe['selling_pressure_ma'] > dataframe['buying_pressure_ma'] * 2),
            1, 0
        )


        order = 4
        group_size = 5

        
        local_support = [np.nan] * len(dataframe)
        local_support_value = [np.nan] * len(dataframe)
        pointwise_slope = [np.nan] * len(dataframe)
        pointwise_value = [np.nan] * len(dataframe)
        
        total_l = len(dataframe)
        # min_idx: 저점 피봇 인덱스 리스트 (미리 구해둔 값이라고 가정)
        prev_group_end_idx = 0
        prev_group_end_val = None
        slope = 0
        intercept = 0

        for i in range(order * 2, total_l):
            min_idx = argrelextrema(dataframe['low'].values[prev_group_end_idx + 1:i + 1], np.less_equal, order=order)[0]
            min_idx = [prev_group_end_idx + x + 1 for x in min_idx] 

            sorted_min_idx = sorted(min_idx, key=lambda x: dataframe['low'].iloc[x])
            l = len(sorted_min_idx)
            if l  <= 2:
                pointwise_slope[i] = slope
                pointwise_value[i] = slope * i + intercept
                continue  # 최소 2개 없으면 이전 기울기 그대로
            
            if l >= group_size: group = sorted_min_idx[:group_size]
            else: group = sorted_min_idx
            
            group = sorted(group)
            x_group = np.array(group)
            y_group = dataframe['low'].iloc[group].values

            if prev_group_end_idx != 0 :
                x_group = np.insert(x_group, 0, prev_group_end_idx)
                y_group = np.insert(y_group, 0, prev_group_end_val)         

            slope = self.fit_slope_through_point(x_group, y_group, x_group[0], y_group[0])
            intercept = y_group[0] - slope * x_group[0]

            pointwise_slope[i] = slope
            pointwise_value[i] = slope * i + intercept
            if l >= group_size: 
                group_start_idx = x_group[0]
                group_end_idx = x_group[-1]

                group_start_val = slope * group_start_idx + intercept
                group_end_val = slope * group_end_idx + intercept
                
                # 현재 그룹 범위 내에서 회귀선을 계산하여 trendline_support에 채워넣음
                for x in range(group_start_idx, group_end_idx + 1):
                    y = slope * x + intercept
                    local_support[x] = y
                
                local_support_value[group_end_idx] = y

                # 이번 그룹의 회귀선 끝점 정보를 저장하여 다음 그룹과 연결
                prev_group_end_idx = group_end_idx
                prev_group_end_val = group_end_val

        dataframe['trendline_support_group'] = local_support
        dataframe['trendline_support_group_last_value'] = local_support_value
        dataframe['trendline_support_slope_pointwise'] = pointwise_slope
        dataframe['trendline_support_value_pointwise'] = pointwise_value


        local_resistance = [np.nan] * len(dataframe)
        local_resistance_value = [np.nan] * len(dataframe)
        pointwise_slope = [np.nan] * len(dataframe)
        pointwise_value = [np.nan] * len(dataframe)

        # 이전 그룹의 회귀선의 끝점 정보를 저장할 변수
        prev_group_end_idx = 0
        prev_group_end_val = None
        slope = 0
        for i in range(order * 2, total_l):
            max_idx = argrelextrema(dataframe['high'].values[prev_group_end_idx + 1:i + 1], np.greater_equal, order=order)[0]
            max_idx = [prev_group_end_idx + x + 1 for x in max_idx] 
        
            sorted_max_idx = sorted(max_idx, key=lambda x: dataframe['high'].iloc[x],reverse=True)
            l = len(sorted_max_idx)
            if l  <= 2 :
                pointwise_slope[i] = slope
                continue  # 최소 2개 없으면 회귀 불가
            
            if l >= group_size: group = sorted_max_idx[:group_size]
            else: group = sorted_max_idx

            group = sorted(group)
            x_group = np.array(group)
            y_group = dataframe['high'].iloc[group].values

            if prev_group_end_idx != 0 :
                x_group = np.insert(x_group, 0, prev_group_end_idx)
                y_group = np.insert(y_group, 0, prev_group_end_val)         
        
            slope = self.fit_slope_through_point(x_group, y_group, x_group[0], y_group[0])
            intercept = y_group[0] - slope * x_group[0]

            pointwise_slope[i] = slope
            pointwise_value[i] = slope * i + intercept
            if l >= group_size: 
                group_start_idx = x_group[0]
                group_end_idx = x_group[-1]
                
                group_start_val = slope * group_start_idx + intercept
                group_end_val = slope * group_end_idx + intercept

                for x in range(group_start_idx, group_end_idx + 1):
                    y = slope * x + intercept
                    local_resistance[x] = y
                    
                local_resistance_value[group_end_idx] = y

                # 이번 그룹의 회귀선 끝점 정보를 저장하여 다음 그룹과 연결
                prev_group_end_idx = group_end_idx
                prev_group_end_val = group_end_val

        dataframe['trendline_resistance_group'] = local_resistance
        dataframe['trendline_resistance_group_last_value'] = local_resistance_value
        dataframe['trendline_resistance_slope_pointwise'] = pointwise_slope
        dataframe['trendline_resistance_value_pointwise'] = pointwise_value

        order = 7
        group_size = 15
        
        dataframe['hloc_avg'] = (dataframe['open'] + dataframe['high'] + dataframe['low'] + dataframe['close']) / 4
        local_support = [np.nan] * len(dataframe)
        local_support_slope = [np.nan] * len(dataframe)
        pointwise_slope = [np.nan] * len(dataframe)
        
        total_l = len(dataframe)
        # min_idx: 저점 피봇 인덱스 리스트 (미리 구해둔 값이라고 가정)
        prev_group_end_idx = 0
        prev_group_end_val = None
        slope = 0
        for i in range(order * 2, total_l):
            min_idx = argrelextrema(dataframe['hloc_avg'].values[prev_group_end_idx + 1:i + 1], np.less_equal, order=order)[0]
            min_idx = [prev_group_end_idx + x + 1 for x in min_idx]

            sorted_min_idx = sorted(min_idx, key=lambda x: dataframe['hloc_avg'].iloc[x])
            l = len(sorted_min_idx)
            if l  <= 2:
                pointwise_slope[i] = slope
                continue  # 최소 2개 없으면 이전 기울기 그대로
            
            group = sorted_min_idx[:group_size] if l >= group_size else sorted_min_idx
            
            group = sorted(group)
            x_group = np.array(group)
            y_group = dataframe['hloc_avg'].iloc[group].values

            if prev_group_end_idx != 0 :
                x_group = np.insert(x_group, 0, prev_group_end_idx)
                y_group = np.insert(y_group, 0, prev_group_end_val)         

            slope = self.fit_slope_through_point(x_group, y_group, x_group[0], y_group[0])
            intercept = y_group[0] - slope * x_group[0]

            pointwise_slope[i] = slope
            if l >= group_size: 
                group_start_idx = x_group[0]
                group_end_idx = x_group[-1]

                group_start_val = slope * group_start_idx + intercept
                group_end_val = slope * group_end_idx + intercept
                
                # 현재 그룹 범위 내에서 회귀선을 계산하여 trendline_support에 채워넣음
                for x in range(group_start_idx, group_end_idx + 1):
                    y = slope * x + intercept
                    local_support[x] = y
                
                local_support_slope[group_end_idx] = slope

                # 이번 그룹의 회귀선 끝점 정보를 저장하여 다음 그룹과 연결
                prev_group_end_idx = group_end_idx
                prev_group_end_val = group_end_val

        dataframe['trendline'] = local_support
        dataframe['trendline_slope'] = local_support_slope
        dataframe['trendline_slope_pointwise'] = pointwise_slope

        dataframe['avg_trendline_support_slope_pointwise'] = dataframe['trendline_support_slope_pointwise'].rolling(window=3).mean()
        dataframe['avg_trendline_resistance_slope_pointwise'] = dataframe['trendline_resistance_slope_pointwise'].rolling(window=3).mean()
        dataframe['avg_trendline_slope_pointwise'] = dataframe['trendline_slope_pointwise'].rolling(window=3).mean()
        
        dataframe['avg_trendline_resistance_value'] = self.past_non_nan_avg(dataframe['trendline_resistance_group_last_value'])
        dataframe['avg_trendline_support_value'] = self.past_non_nan_avg(dataframe['trendline_support_group_last_value'])
        dataframe[['date', 'trendline_support_group_last_value', 'avg_trendline_support_value', "trendline_support_value_pointwise" ]].to_csv('/freqtrade/user_data/avg_trendline_support_value.csv', index=False)
        dataframe[['date', 'trendline_resistance_group_last_value', 'avg_trendline_resistance_value', "trendline_resistance_value_pointwise" ]].to_csv('/freqtrade/user_data/avg_trendline_resistance_value.csv', index=False)


        btc_slopes = dataframe['trendline_support_slope_pointwise'].dropna()
        positive_slopes = btc_slopes[btc_slopes > 0].mean()
        negative_slopes = btc_slopes[btc_slopes < 0].mean()
        print(f"10 support: {10 / positive_slopes}")
        print(f"5 support: {5 / positive_slopes} ")
        print(f"30 support: {30 / positive_slopes} ")

        print(f"-10 support: {-10 / negative_slopes}")
        print(f"-5 support: {-5 / negative_slopes} ")
        print(f"-30 support: {-30 / negative_slopes} ")

        btc_slopes = dataframe['trendline_resistance_slope_pointwise'].dropna()
        positive_slopes = btc_slopes[btc_slopes > 0].mean()
        negative_slopes = btc_slopes[btc_slopes < 0].mean()
        print(f"10 resistance: {10 / positive_slopes}")
        print(f"5 resistance: {5 / positive_slopes} ")
        print(f"30 resistance: {30 / positive_slopes} ")

        print(f"-10 resistance: {-10 / negative_slopes}")
        print(f"-5 resistance: {-5 / negative_slopes} ")
        print(f"-30 resistance: {-30 / negative_slopes} ")


        btc_slopes = dataframe['avg_trendline_slope_pointwise'].dropna()
        positive_slopes = btc_slopes[btc_slopes > 0].mean()
        negative_slopes = btc_slopes[btc_slopes < 0].mean()
        print(f"10 avg trendline: {10 / positive_slopes}")
        print(f"5 avg trendline: {5 / positive_slopes} ")
        print(f"-10 avg trendline: {-10 / negative_slopes}")
        print(f"-5 avg trendline: {-5 / negative_slopes} ")

        btc_slopes = dataframe['trendline_slope_pointwise'].dropna()
        positive_slopes = btc_slopes[btc_slopes > 0].mean()
        negative_slopes = btc_slopes[btc_slopes < 0].mean()
        print(f"10 trendline: {10 / positive_slopes}")
        print(f"5 trendline: {5 / positive_slopes} ")
        print(f"15 trendline: {15 / positive_slopes} ")

        print(f"-10 trendline: {-10 / negative_slopes}")
        print(f"-5 trendline: {-5 / negative_slopes} ")
        print(f"-15 trendline: {-15 / negative_slopes} ")

        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        print("📊 Checking entry conditions...")
        support_slopes = dataframe['trendline_support_slope_pointwise'].dropna()
        resistance_slopes = dataframe['trendline_resistance_slope_pointwise'].dropna()
        avg_trendline_slopes = dataframe['avg_trendline_slope_pointwise'].dropna()
        trendline_slopes = dataframe['trendline_slope_pointwise'].dropna()

        # long - 매수세 지표 추가
        dataframe.loc[
            ((dataframe['rsi_14'] > 50) & (dataframe['rsi_14'] < 70)) &
            (dataframe['trendline_slope_pointwise'] > trendline_slopes[trendline_slopes > 0].mean() * 0.3) &
            (dataframe['avg_trendline_support_slope_pointwise'] >= avg_trendline_slopes[avg_trendline_slopes > 0].mean() * 0.3) & 
            (dataframe['avg_trendline_support_value'] < dataframe['trendline_support_value_pointwise']) &
            # 매수세 강화 조건 추가
            ((dataframe['pressure_balance'] > 0) | (dataframe['strong_buying_signal'] == 1)) &
            (dataframe['pressure_ratio'] > 1.2),  # 매수세가 매도세보다 20% 이상 강함
            ['enter_long', 'enter_tag']
        ] = (1, 'up_trend_long_with_buying_pressure')

        # short - 매도세 지표 추가
        dataframe.loc[
            ((dataframe['rsi_14'] < 50) & (dataframe['rsi_14'] > 30)) &
            (dataframe['trendline_slope_pointwise'] < trendline_slopes[trendline_slopes < 0].mean() * 0.3) & 
            (dataframe['avg_trendline_resistance_slope_pointwise'] <= avg_trendline_slopes[avg_trendline_slopes < 0].mean() * 0.3) &
            (dataframe['avg_trendline_resistance_value'] > dataframe['trendline_resistance_value_pointwise']) &
            # 매도세 강화 조건 추가
            ((dataframe['pressure_balance'] < 0) | (dataframe['strong_selling_signal'] == 1)) &
            (dataframe['pressure_ratio'] < 0.8),  # 매도세가 매수세보다 20% 이상 강함
            ['enter_short', 'enter_tag']
        ] = (1, 'down_trend_short_with_selling_pressure')

        # NaN 값을 0으로 채움
        # dataframe["enter_long"] = dataframe["enter_long"].fillna(0)
        # dataframe["enter_short"] = dataframe["enter_short"].fillna(0)
        # dataframe["enter_tag"] = dataframe["enter_tag"].fillna("no_entry")

        # # 각 enter_tag별로 신호 개수를 계산
        # enter_tag_counts = dataframe["enter_tag"].value_counts()

        # print("📌 Entry Signal Counts:")
        # print("Long entries:")
        # print(f"  Trendlien and RSI or MACD Long: {enter_tag_counts.get('rsi_or_macd_long', 0)}")
        # # print(f"  MACD Pullback Long: {enter_tag_counts.get('macd_pullback_long', 0)}")
        # # print(f"  Pullback Long: {enter_tag_counts.get('pullback_long', 0)}")
        # # print(f"  RSI bb Long: {enter_tag_counts.get('rsi_bb_long', 0)}")
        # print("Short entries:")
        # # print(f"  Pullback Short: {enter_tag_counts.get('pullback_short', 0)}")
        # print(f"  Trendlien and RSI or MACD Short: {enter_tag_counts.get('rsi_or_macd_short', 0)}")
        # print(f"No Entry: {enter_tag_counts.get('no_entry', 0)}")

        # print("Total Long entries:", dataframe["enter_long"].sum())
        # print("Total Short entries:", dataframe["enter_short"].sum())
        return dataframe



    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        print("📌 Checking exit conditions...")

        # 5: 0.2, 10: 0.4
        support_slopes = dataframe['trendline_support_slope_pointwise'].dropna()
        resistance_slopes = dataframe['trendline_resistance_slope_pointwise'].dropna()

        # 5: 0.3, 10: 0.6
        avg_trendline_slopes = dataframe['avg_trendline_slope_pointwise'].dropna()

        # exit_tag 초기화
        dataframe['exit_tag'] = ''

        # 하락 조짐 (롱 청산) - 매도세 강화
        down_trend = (
            (dataframe['trendline_support_slope_pointwise'] < support_slopes[support_slopes < 0].mean() * 0.2) &
            (dataframe['trendline_resistance_slope_pointwise'] < resistance_slopes[resistance_slopes < 0].mean() * 0.4) &
            (dataframe['avg_trendline_slope_pointwise'] < avg_trendline_slopes[avg_trendline_slopes < 0].mean() * 0.6) &
            # 매도세 강화 조건 추가
            ((dataframe['pressure_balance'] < 0) | (dataframe['strong_selling_signal'] == 1))
        )
        dataframe.loc[down_trend, 'exit_long'] = 1
        dataframe.loc[down_trend, 'exit_tag'] += 'down_trend_long_with_selling_pressure '
       

        # 강한 상승 조건 (롱 포지션 종료) - 매수세 약화
        strong_uptrend = (
            (dataframe['rsi_14'] > 70) &
            (
                (dataframe['trendline_support_slope_pointwise'] > support_slopes[support_slopes > 0].mean() * 0.4) &
                (dataframe['avg_trendline_slope_pointwise'] < avg_trendline_slopes[avg_trendline_slopes > 0].mean() * 0.3)
            ) | # 횡보장 + 급 상승)
            ((dataframe['trendline_resistance_slope_pointwise'] > resistance_slopes[resistance_slopes > 0].mean() * 1.1) & (dataframe['avg_trendline_slope_pointwise'] >  avg_trendline_slopes[avg_trendline_slopes > 0].mean() * 0.6)) |
            # 매수세 약화 조건 추가
            (dataframe['pressure_ratio'] < 0.5)  # 매수세가 매도세의 절반 이하로 약화
        )
        dataframe.loc[strong_uptrend, 'exit_long'] = 1
        dataframe.loc[strong_uptrend, 'exit_tag'] += 'trendline_strong_long_with_weak_buying_pressure'


        # 상승 조짐 (숏 청산) - 매수세 강화
        up_trend = (
            ((dataframe['trendline_support_slope_pointwise'] > support_slopes[support_slopes > 0].mean() * 0.4) &
            (dataframe['trendline_resistance_slope_pointwise'] > resistance_slopes[resistance_slopes > 0].mean() * 0.2)) &
            (dataframe['avg_trendline_slope_pointwise'] > avg_trendline_slopes[avg_trendline_slopes > 0].mean() * 0.6) &
            # 매수세 강화 조건 추가
            ((dataframe['pressure_balance'] > 0) | (dataframe['strong_buying_signal'] == 1))
        )
        dataframe.loc[up_trend, 'exit_short'] = 1
        dataframe.loc[up_trend, 'exit_tag'] += 'up_trend_short_with_buying_pressure '

        #강한 하락 (숏 청산) - 매도세 약화
        strong_short = (
            (dataframe['rsi_14'] < 30) &
            (
                (dataframe['trendline_resistance_slope_pointwise'] < resistance_slopes[resistance_slopes < 0].mean() * 0.4) &
                (dataframe['avg_trendline_slope_pointwise'] > avg_trendline_slopes[avg_trendline_slopes < 0].mean() * 0.3)
            ) |
            ((dataframe['trendline_support_slope_pointwise'] < support_slopes[support_slopes < 0].mean() * 1.1) & (dataframe['avg_trendline_slope_pointwise'] < avg_trendline_slopes[avg_trendline_slopes < 0].mean() * 0.6)) |
            # 매도세 약화 조건 추가
            (dataframe['pressure_ratio'] > 2.0)  # 매수세가 매도세의 2배 이상으로 강화
        )
        dataframe.loc[strong_short, 'exit_short'] = 1
        dataframe.loc[strong_short, 'exit_tag'] += 'trendline_strong_short_with_weak_selling_pressure'
   

        # dataframe["exit_long"] = dataframe["exit_long"].fillna(0)
        # dataframe["exit_short"] = dataframe["exit_short"].fillna(0)
        # dataframe["exit_tag"] = dataframe["exit_tag"].fillna("no_exit")

        # # 각 exit_tag 별로 신호 개수를 계산
        # enter_tag_counts = dataframe["exit_tag"].value_counts()
        # print("📌 Exit Signal Counts:")
        # print("Long exits:")
        # # print(f"  Trendlien and RSI or MACD Long: {enter_tag_counts.get('rsi_or_macd_long', 0)}")
        # # print(f"  Candlestick Long: {enter_tag_counts.get('candlestick_long', 0)}")
        # print("Short exits:")
        # # print(f"  RSI or MACD Short: {enter_tag_counts.get('rsi_or_macd_short', 0)}")
        # # print(f"  candlestick Short: {enter_tag_counts.get('candlestick_short', 0)}")
        # print(f"No Entry: {enter_tag_counts.get('no_entry', 0)}")

        # print("Total Long exits:", dataframe["exit_long"].sum())
        # print("Total Short exits:", dataframe["exit_short"].sum())
        return dataframe

    def custom_stoploss(self, pair: str, trade: Trade, current_time: datetime, current_rate: float, current_profit: float, **kwargs) -> float:
        dataframe, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
        last_candle = dataframe.iloc[-1].squeeze()

        trendline_slopes = dataframe['trendline_slope_pointwise'].dropna()

        # if not trade.is_short and (last_candle['avg_trendline_support_value'] < last_candle['trendline_support_value_pointwise']):
        #     return -0.1  
        # if trade.is_short and (last_candle['avg_trendline_resistance_value'] > last_candle['trendline_resistance_value_pointwise']):
        #     return -0.1  

        if not trade.is_short and (last_candle['trendline_slope_pointwise'] < trendline_slopes[trendline_slopes > 0].mean() * 0.9 or (last_candle['avg_trendline_support_value'] < last_candle['trendline_support_value_pointwise'])):
            return -0.07
        if trade.is_short and (last_candle['trendline_slope_pointwise'] > trendline_slopes[trendline_slopes < 0].mean() * 0.9 or (last_candle['avg_trendline_resistance_value'] > last_candle['trendline_resistance_value_pointwise'])):
            return -0.07 

        # 그 외 기본 stoploss 적용
        return -0.04


    def confirm_trade_exit(
        self,
        pair: str,
        trade: Trade,
        order_type: str,
        amount: float,
        rate: float,
        time_in_force: str,
        exit_reason: str,
        current_time: datetime,
        **kwargs,
    ) -> bool:
        dataframe, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
        last_candle = dataframe.iloc[-1].squeeze()
        current_rate = last_candle["close"]

        current_profit = trade.calc_profit(current_rate)
        
        # if (trade.is_short and (last_candle['avg_trendline_resistance_value'] > last_candle['trendline_resistance_value_pointwise'])):
        #     return False
        
        # if (not trade.is_short and (last_candle['avg_trendline_support_value'] < last_candle['trendline_support_value_pointwise'])):
        #     return False
        return True  # 정상적으로 exit 진행
    

    # def custom_exit(self, pair: str, trade: 'Trade', current_time: 'datetime', 
    #             current_rate: float, current_profit: float, **kwargs):

    #     if current_profit >= 0.05:
    #         return "roi_exit" 

    #     return  None



    # def custom_entry_position(self, pair: str, trade: Trade, current_time: datetime, **kwargs) -> float:
    #     dataframe, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
    #     last_candle = dataframe.iloc[-1]
        
    #     # atr = last_candle["atr"]
    #     # risk_factor = 0.02  # 📌 한 번의 손절당 최대 2% 손실을 목표로 설정
    #     # position_size = risk_factor / atr  # ATR을 기반으로 포지션 크기 조정
        
    #     # return min(position_size, 0.1)  # 📌 최대 포지션 크기를 10%로 제한
