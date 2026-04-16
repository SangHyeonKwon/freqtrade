"""
캔들 차트 데이터 기반 자동 추세선 라이브러리

이 라이브러리는 OHLCV 캔들 차트 데이터를 기반으로 지지선과 저항선을 자동으로 계산합니다.
주요 기능:
- 극값(extrema) 자동 탐지
- 선형 회귀를 이용한 추세선 계산
- 스무딩을 통한 부드러운 추세선 생성
- 실시간 추세선 값 및 기울기 계산
"""

import numpy as np
import pandas as pd
from pandas import DataFrame
from scipy.signal import argrelextrema
from typing import Dict, Tuple, Optional


def calculate_support_trendline(
    dataframe: DataFrame,
    lookback: int = 150,
    order: int = 8,
    smooth_window: int = 30
) -> Tuple[pd.Series, float]:
    """
    캔들 차트 데이터를 기반으로 지지선 추세선을 계산합니다.
    
    Args:
        dataframe: OHLCV 데이터가 포함된 DataFrame
        lookback: 추세선 계산에 사용할 최근 캔들 수
        order: 극값 탐지 민감도 (더 큰 값 = 더 중요한 극값만 선택)
        smooth_window: 스무딩을 위한 윈도우 크기
    
    Returns:
        Tuple[pd.Series, float]: (지지선 값 시리즈, 기울기 값)
    """
    if len(dataframe) >= lookback:
        # 전체 기간의 저점 찾기
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
    smoothed_support = support_series.rolling(window=smooth_window, center=True).mean().bfill().ffill()
    
    return smoothed_support, slope_val


def calculate_resistance_trendline(
    dataframe: DataFrame,
    lookback: int = 150,
    order: int = 8,
    smooth_window: int = 30
) -> Tuple[pd.Series, float]:
    """
    캔들 차트 데이터를 기반으로 저항선 추세선을 계산합니다.
    
    Args:
        dataframe: OHLCV 데이터가 포함된 DataFrame
        lookback: 추세선 계산에 사용할 최근 캔들 수
        order: 극값 탐지 민감도 (더 큰 값 = 더 중요한 극값만 선택)
        smooth_window: 스무딩을 위한 윈도우 크기
    
    Returns:
        Tuple[pd.Series, float]: (저항선 값 시리즈, 기울기 값)
    """
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
    smoothed_resistance = resistance_series.rolling(window=smooth_window, center=True).mean().bfill().ffill()
    
    return smoothed_resistance, slope_val


def calculate_trendlines(
    dataframe: DataFrame,
    lookback: int = 150,
    order: int = 8,
    smooth_window: int = 30
) -> Dict[str, pd.Series | float]:
    """
    지지선과 저항선을 모두 계산하여 반환합니다.
    
    Args:
        dataframe: OHLCV 데이터가 포함된 DataFrame
        lookback: 추세선 계산에 사용할 최근 캔들 수
        order: 극값 탐지 민감도
        smooth_window: 스무딩을 위한 윈도우 크기
    
    Returns:
        Dict: {
            'support_values': 지지선 값 시리즈,
            'support_slope': 지지선 기울기,
            'resistance_values': 저항선 값 시리즈,
            'resistance_slope': 저항선 기울기
        }
    """
    support_values, support_slope = calculate_support_trendline(
        dataframe, lookback, order, smooth_window
    )
    resistance_values, resistance_slope = calculate_resistance_trendline(
        dataframe, lookback, order, smooth_window
    )
    
    return {
        'support_values': support_values,
        'support_slope': support_slope,
        'resistance_values': resistance_values,
        'resistance_slope': resistance_slope
    }


def fit_slope_through_point(x: np.ndarray, y: np.ndarray, x0: float, y0: float) -> float:
    """
    특정 점 (x0, y0)을 지나면서 주어진 점들에 가장 잘 맞는 직선의 기울기를 계산합니다.
    가중치를 사용하여 최근 데이터에 더 큰 가중치를 부여합니다.
    
    Args:
        x: X 좌표 배열
        y: Y 좌표 배열
        x0: 기준점 X 좌표
        y0: 기준점 Y 좌표
    
    Returns:
        float: 계산된 기울기 값
    """
    x_shifted = x - x0
    y_shifted = y - y0
    weights = np.linspace(1, 4, len(x))
    numerator = np.sum(weights * x_shifted * y_shifted)
    denominator = np.sum(weights * x_shifted ** 2)
    if denominator == 0:
        return 0
    return numerator / denominator

