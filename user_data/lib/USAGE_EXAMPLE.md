# 라이브러리 사용 예시

## Freqtrade 전략에서 사용하기

### 1. 라이브러리 Import

```python
import sys
from pathlib import Path

# user_data 디렉토리를 Python path에 추가
sys.path.insert(0, str(Path(__file__).parent.parent))

# 라이브러리 함수 import
from lib.trendline_lib import (
    calculate_support_trendline,
    calculate_resistance_trendline,
    fit_slope_through_point
)
```

### 2. 전략에서 사용

```python
def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
    # ... 기타 지표 계산 ...
    
    # === 자동 추세선 계산 (라이브러리 사용) ===
    lookback = 150
    order = 8
    smooth_window = 30
    
    # 지지선 계산
    support_values, support_slope = calculate_support_trendline(
        dataframe, 
        lookback=lookback, 
        order=order, 
        smooth_window=smooth_window
    )
    dataframe['trendline_support_value_pointwise'] = support_values
    dataframe['trendline_support_slope_pointwise'] = support_slope
    
    # 저항선 계산
    resistance_values, resistance_slope = calculate_resistance_trendline(
        dataframe, 
        lookback=lookback, 
        order=order, 
        smooth_window=smooth_window
    )
    dataframe['trendline_resistance_value_pointwise'] = resistance_values
    dataframe['trendline_resistance_slope_pointwise'] = resistance_slope
    
    return dataframe
```

### 3. 진입/청산 조건에서 활용

```python
def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
    dataframe.loc[
        (
            # 지지선 근처에서 매수
            (dataframe['close'] > dataframe['trendline_support_value_pointwise'] * 0.998) &
            # 지지선 기울기가 양수 (상승 추세)
            (dataframe['trendline_support_slope_pointwise'] > 0) &
            # 기타 조건...
        ),
        'enter_long'
    ] = 1
    return dataframe
```

## 디렉토리 구조

```
user_data/
├── lib/
│   ├── __init__.py
│   ├── trendline_lib.py      # 메인 라이브러리 파일
│   ├── README.md             # 라이브러리 설명서
│   └── USAGE_EXAMPLE.md      # 사용 예시 (이 파일)
└── strategies/
    └── TrendPro.py           # 라이브러리를 사용하는 전략 예시
```

