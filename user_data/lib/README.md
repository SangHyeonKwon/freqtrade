# 캔들 차트 데이터 기반 자동 추세선 라이브러리

## 개요
이 라이브러리는 OHLCV 캔들 차트 데이터를 기반으로 지지선과 저항선을 자동으로 계산하는 Python 라이브러리입니다.

## 주요 기능
- **극값(extrema) 자동 탐지**: scipy.signal.argrelextrema를 활용한 고/저점 자동 탐지
- **선형 회귀 기반 추세선 계산**: numpy.polyfit을 이용한 정확한 추세선 계산
- **스무딩 처리**: 이동평균을 통한 부드러운 추세선 생성
- **실시간 계산**: 실시간 추세선 값 및 기울기 계산

## 제공 함수

### 1. calculate_support_trendline()
지지선 추세선을 계산합니다.

**매개변수:**
- `dataframe`: OHLCV 데이터가 포함된 DataFrame
- `lookback`: 추세선 계산에 사용할 최근 캔들 수 (기본값: 150)
- `order`: 극값 탐지 민감도 (기본값: 8)
- `smooth_window`: 스무딩을 위한 윈도우 크기 (기본값: 30)

**반환값:**
- `Tuple[pd.Series, float]`: (지지선 값 시리즈, 기울기 값)

### 2. calculate_resistance_trendline()
저항선 추세선을 계산합니다.

**매개변수:**
- `dataframe`: OHLCV 데이터가 포함된 DataFrame
- `lookback`: 추세선 계산에 사용할 최근 캔들 수 (기본값: 150)
- `order`: 극값 탐지 민감도 (기본값: 8)
- `smooth_window`: 스무딩을 위한 윈도우 크기 (기본값: 30)

**반환값:**
- `Tuple[pd.Series, float]`: (저항선 값 시리즈, 기울기 값)

### 3. calculate_trendlines()
지지선과 저항선을 모두 계산하여 반환합니다.

### 4. fit_slope_through_point()
특정 점을 지나면서 주어진 점들에 가장 잘 맞는 직선의 기울기를 계산합니다.

## 사용 예시

```python
# 라이브러리 import
from lib.trendline_lib import (
    calculate_support_trendline,
    calculate_resistance_trendline
)

# 지지선 계산
support_values, support_slope = calculate_support_trendline(
    dataframe, 
    lookback=150, 
    order=8, 
    smooth_window=30
)

# 저항선 계산
resistance_values, resistance_slope = calculate_resistance_trendline(
    dataframe, 
    lookback=150, 
    order=8, 
    smooth_window=30
)

# DataFrame에 추가
dataframe['trendline_support'] = support_values
dataframe['trendline_resistance'] = resistance_values
```

## 의존성
- numpy
- pandas
- scipy

