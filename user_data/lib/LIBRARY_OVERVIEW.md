# 라이브러리 개요 (PPT용)

## 1. 라이브러리 구조 및 주요 함수

### 📁 디렉토리 구조
```
user_data/lib/
├── __init__.py
└── trendline_lib.py  (총 194줄)
```

### 🔧 주요 함수 4개

#### 1. `calculate_support_trendline()` - 지지선 계산
```python
def calculate_support_trendline(
    dataframe: DataFrame,
    lookback: int = 150,
    order: int = 8,
    smooth_window: int = 30
) -> Tuple[pd.Series, float]:
    """
    캔들 차트 데이터를 기반으로 지지선 추세선을 계산합니다.
    - 극값(extrema) 자동 탐지
    - 선형 회귀를 이용한 추세선 계산
    - 스무딩을 통한 부드러운 추세선 생성
    
    Returns:
        (지지선 값 시리즈, 기울기 값)
    """
```

#### 2. `calculate_resistance_trendline()` - 저항선 계산
```python
def calculate_resistance_trendline(
    dataframe: DataFrame,
    lookback: int = 150,
    order: int = 8,
    smooth_window: int = 30
) -> Tuple[pd.Series, float]:
    """
    캔들 차트 데이터를 기반으로 저항선 추세선을 계산합니다.
    """
```

#### 3. `calculate_trendlines()` - 지지선/저항선 동시 계산
#### 4. `fit_slope_through_point()` - 특정 점을 지나는 기울기 계산

---

## 2. 전략에서 라이브러리 사용 예시

### Import 부분
```python
# user_data 디렉토리를 Python path에 추가
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

# 라이브러리 함수 import
from lib.trendline_lib import (
    calculate_support_trendline,
    calculate_resistance_trendline,
    fit_slope_through_point
)
```

### 사용 예시
```python
def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
    # === 자동 추세선 계산 (라이브러리 사용) ===
    lookback = 150
    order = 8
    smooth_window = 30
    
    # 지지선 계산 (라이브러리 함수 호출)
    support_values, support_slope = calculate_support_trendline(
        dataframe, 
        lookback=lookback, 
        order=order, 
        smooth_window=smooth_window
    )
    dataframe['trendline_support_value_pointwise'] = support_values
    dataframe['trendline_support_slope_pointwise'] = support_slope
    
    # 저항선 계산 (라이브러리 함수 호출)
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

---

## 3. 라이브러리 특징

✅ **모듈화**: 전략 코드와 분리된 독립적인 라이브러리  
✅ **재사용성**: 다른 전략에서도 쉽게 사용 가능  
✅ **자동화**: 극값 탐지부터 추세선 계산까지 자동 처리  
✅ **유연성**: 파라미터 조정으로 다양한 시장 상황 대응

