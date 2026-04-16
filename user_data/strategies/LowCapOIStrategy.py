# --- 필수 import 구문 ---
import numpy as np
import pandas as pd
from pandas import DataFrame
from freqtrade.strategy import (
    IStrategy,
    IntParameter,
    CategoricalParameter,
    DecimalParameter,
    merge_informative_pair,
)
import talib.abstract as ta
import logging
from typing import Optional
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)


class LowCapOIStrategy(IStrategy):
    """
    바이낸스 저시총 알트 코인 OI(Open Interest) 기반 역추세 전략
    
    핵심 전략:
    - 넓은 타임프레임에서 가격이 하락하는데 OI는 계속 증가하는 패턴 식별
    - 바이낸스의 Mark Price vs Last Price 갭 활용
    - 이런 패턴에서 롱 진입 (반등 가능성)
    
    참고: 이 전략은 dry-run에 적용하지 않고 백테스팅/테스트용으로만 사용합니다.
    """
    
    # 기본 설정
    INTERFACE_VERSION = 3
    timeframe = '15m'
    informative_timeframe = '4h'  # 넓은 타임프레임으로 변경
    wide_timeframe = '1d'  # 더 넓은 타임프레임 추가
    can_short = True
    startup_candle_count = 300
    
    # 리스크 관리
    stoploss = -0.05  # 5% 손절
    trailing_stop = True
    trailing_stop_positive = 0.02
    trailing_stop_positive_offset = 0.025
    
    # ROI 설정
    minimal_roi = {
        "0": 0.05,
        "30": 0.03,
        "60": 0.02,
        "120": 0.01
    }
    
    # === Hyperopt 파라미터 ===
    # OI 관련 파라미터
    oi_change_threshold = DecimalParameter(0.1, 0.5, default=0.2, space="buy")
    oi_momentum_period = IntParameter(5, 20, default=10, space="buy")
    min_oi_value = DecimalParameter(100000, 5000000, default=1000000, space="buy")
    
    # 시평갭 관련 파라미터 (Mark Price vs Last Price)
    price_gap_threshold = DecimalParameter(0.001, 0.02, default=0.005, space="buy")  # 0.1% ~ 2%
    price_gap_max = DecimalParameter(0.01, 0.05, default=0.03, space="buy")  # 최대 갭
    
    # 역추세 패턴 파라미터
    price_decline_period = IntParameter(5, 20, default=10, space="buy")  # 가격 하락 기간
    oi_increase_period = IntParameter(3, 15, default=7, space="buy")  # OI 증가 기간
    price_decline_threshold = DecimalParameter(0.05, 0.30, default=0.15, space="buy")  # 가격 하락 임계값
    
    # RSI 파라미터
    buy_rsi = IntParameter(25, 45, default=35, space="buy")
    sell_rsi = IntParameter(50, 70, default=60, space="sell")
    
    # 볼륨 파라미터
    volume_multiplier = DecimalParameter(1.2, 3.0, default=1.5, space="buy")
    
    def informative_pairs(self):
        """인포메이티브 페어 설정"""
        # self.dp가 없을 수 있으므로 안전하게 처리
        pairs = []
        try:
            if self.dp and hasattr(self.dp, 'current_whitelist'):
                pairs = self.dp.current_whitelist()
        except Exception:
            # 백테스팅 시작 시점에는 whitelist가 없을 수 있음
            pass
        
        if not pairs:
            # 빈 리스트를 반환하면 freqtrade가 자동으로 화이트리스트 페어를 사용
            return []
        
        informative_pairs = [
            (pair, self.informative_timeframe) for pair in pairs
        ]
        informative_pairs.extend([
            (pair, self.wide_timeframe) for pair in pairs
        ])
        return informative_pairs
    
    def fetch_open_interest(self, pair: str) -> Optional[float]:
        """
        바이낸스 선물에서 OI 데이터를 가져옵니다.
        백테스팅에서는 None을 반환합니다.
        """
        try:
            # 백테스팅 모드에서는 OI 데이터를 사용할 수 없음
            if hasattr(self.dp, '_exchange') and self.dp._exchange:
                exchange = self.dp._exchange
                
                # 바이낸스 선물만 지원
                if exchange.name != 'binance':
                    logger.warning(f"OI 데이터는 바이낸스에서만 지원됩니다. 현재 거래소: {exchange.name}")
                    return None
                
                # 선물 거래 모드인지 확인
                if exchange.trading_mode.value != 'futures':
                    logger.warning("OI 데이터는 선물 거래 모드에서만 사용 가능합니다.")
                    return None
                
                # ccxt API를 통해 OI 데이터 가져오기
                if hasattr(exchange, '_api') and exchange._api:
                    try:
                        # 바이낸스 선물 페어 형식 변환 (BTC/USDT:USDT -> BTCUSDT)
                        symbol = pair.replace('/', '').replace(':USDT', 'USDT')
                        
                        # fetch_open_interest 호출
                        oi_data = exchange._api.fetch_open_interest(symbol)
                        
                        if oi_data and 'openInterestAmount' in oi_data:
                            return float(oi_data['openInterestAmount'])
                        elif oi_data and 'openInterest' in oi_data:
                            return float(oi_data['openInterest'])
                        elif isinstance(oi_data, (int, float)):
                            return float(oi_data)
                    except Exception as e:
                        logger.warning(f"OI 데이터 가져오기 실패 ({pair}): {e}")
                        return None
        except Exception as e:
            logger.warning(f"OI 데이터 가져오기 중 오류 ({pair}): {e}")
        
        return None
    
    def get_oi_change_rate(self, pair: str, period: int = 10) -> Optional[float]:
        """
        OI 변화율을 계산합니다 (백테스팅에서는 사용 불가)
        """
        # 백테스팅에서는 사용할 수 없으므로 None 반환
        # 실제 트레이딩에서는 캐싱된 OI 데이터를 사용하여 변화율 계산
        return None
    
    def fetch_price_gap(self, pair: str) -> Optional[dict]:
        """
        바이낸스에서 Mark Price와 Last Price의 갭을 가져옵니다.
        시평갭 = (Mark Price - Last Price) / Last Price
        
        Returns:
            dict with keys: 'mark_price', 'last_price', 'index_price', 'gap_percent', 'gap_abs'
        """
        try:
            if hasattr(self.dp, '_exchange') and self.dp._exchange:
                exchange = self.dp._exchange
                
                if exchange.name != 'binance' or exchange.trading_mode.value != 'futures':
                    return None
                
                # Funding rate 정보에서 markPrice와 indexPrice를 가져올 수 있음
                try:
                    funding_data = self.dp.funding_rate(pair)
                    
                    if funding_data and 'markPrice' in funding_data:
                        mark_price = float(funding_data['markPrice'])
                        index_price = float(funding_data.get('indexPrice', mark_price))
                        
                        # Ticker에서 last price 가져오기
                        ticker = self.dp.ticker(pair)
                        last_price = float(ticker.get('last', mark_price)) if ticker else mark_price
                        
                        # 갭 계산
                        gap_abs = mark_price - last_price
                        gap_percent = (gap_abs / last_price) * 100 if last_price > 0 else 0
                        
                        return {
                            'mark_price': mark_price,
                            'last_price': last_price,
                            'index_price': index_price,
                            'gap_percent': gap_percent,
                            'gap_abs': gap_abs
                        }
                except Exception as e:
                    logger.warning(f"시평갭 데이터 가져오기 실패 ({pair}): {e}")
                    return None
        except Exception as e:
            logger.warning(f"시평갭 계산 중 오류 ({pair}): {e}")
        
        return None
    
    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        """
        인디케이터 계산
        """
        # === 기본 지표 ===
        dataframe['rsi_14'] = ta.RSI(dataframe['close'], timeperiod=14)
        dataframe['rsi_21'] = ta.RSI(dataframe['close'], timeperiod=21)
        dataframe['adx'] = ta.ADX(dataframe, timeperiod=14)
        dataframe['atr'] = ta.ATR(dataframe, timeperiod=14)
        
        # MACD
        macd = ta.MACD(dataframe)
        dataframe['macd'] = macd['macd']
        dataframe['macdsignal'] = macd['macdsignal']
        dataframe['macdhist'] = macd['macdhist']
        
        # 볼린저 밴드
        bollinger = ta.BBANDS(dataframe, timeperiod=20, nbdevup=2.0, nbdevdn=2.0)
        dataframe['bb_lower'] = bollinger['lowerband']
        dataframe['bb_middle'] = bollinger['middleband']
        dataframe['bb_upper'] = bollinger['upperband']
        # 0으로 나누기 방지
        bb_range = dataframe['bb_upper'] - dataframe['bb_lower']
        dataframe['bb_percent'] = np.where(
            bb_range != 0,
            (dataframe['close'] - dataframe['bb_lower']) / bb_range,
            0.5  # 기본값
        )
        
        # 볼륨 지표
        dataframe['volume_sma'] = dataframe['volume'].rolling(window=20).mean()
        # 0으로 나누기 방지
        dataframe['volume_ratio'] = np.where(
            dataframe['volume_sma'] != 0,
            dataframe['volume'] / dataframe['volume_sma'],
            1.0  # 기본값
        )
        
        # EMA
        dataframe['ema_9'] = ta.EMA(dataframe['close'], timeperiod=9)
        dataframe['ema_21'] = ta.EMA(dataframe['close'], timeperiod=21)
        dataframe['ema_50'] = ta.EMA(dataframe['close'], timeperiod=50)
        
        # === OI 관련 지표 (백테스팅에서는 사용 불가) ===
        # 실제 트레이딩 모드에서만 OI 데이터를 가져올 수 있음
        pair = metadata.get('pair', '')
        oi_value = self.fetch_open_interest(pair)
        
        if oi_value is not None:
            dataframe['oi_value'] = oi_value
            # OI가 최소값보다 큰지 확인
            dataframe['oi_valid'] = (oi_value >= self.min_oi_value.value).astype(int)
        else:
            # 백테스팅 모드에서는 OI 데이터를 사용할 수 없으므로 기본값 설정
            dataframe['oi_value'] = 0
            dataframe['oi_valid'] = 1  # 백테스팅에서는 항상 유효한 것으로 간주
        
        # === 시평갭 데이터 (Mark Price vs Last Price) ===
        price_gap_data = self.fetch_price_gap(pair)
        if price_gap_data:
            dataframe['mark_price'] = price_gap_data['mark_price']
            dataframe['price_gap_percent'] = price_gap_data['gap_percent']
            dataframe['price_gap_abs'] = price_gap_data['gap_abs']
            dataframe['price_gap_valid'] = (
                (abs(price_gap_data['gap_percent']) >= self.price_gap_threshold.value * 100) &
                (abs(price_gap_data['gap_percent']) <= self.price_gap_max.value * 100)
            ).astype(int)
        else:
            # 백테스팅에서는 기본값
            dataframe['mark_price'] = dataframe['close']
            dataframe['price_gap_percent'] = 0
            dataframe['price_gap_abs'] = 0
            dataframe['price_gap_valid'] = 1
        
        # === 인포메이티브 타임프레임 데이터 (4h) ===
        # 컬럼명 생성 (merge_informative_pair가 자동으로 타임프레임 추가)
        price_declining_col = f'price_declining_{self.informative_timeframe}'
        trend_inf_col = f'trend_{self.informative_timeframe}'
        rsi_inf_col = f'rsi_{self.informative_timeframe}'
        
        # 기본값 먼저 설정 (데이터가 없을 경우를 대비)
        dataframe[price_declining_col] = 0
        dataframe[trend_inf_col] = 0
        dataframe[rsi_inf_col] = 50
        
        if self.dp:
            try:
                inf_tf = self.dp.get_pair_dataframe(pair=pair, timeframe=self.informative_timeframe)
                if inf_tf is not None and not inf_tf.empty and len(inf_tf) > 50:  # 최소 데이터 확인
                    inf_tf['rsi'] = ta.RSI(inf_tf['close'], timeperiod=14)
                    inf_tf['ema_50'] = ta.EMA(inf_tf['close'], timeperiod=50)
                    inf_tf['ema_100'] = ta.EMA(inf_tf['close'], timeperiod=100)
                    
                    # 넓은 타임프레임에서 가격 하락 추세 확인
                    inf_tf['price_change'] = inf_tf['close'].pct_change(periods=self.price_decline_period.value)
                    inf_tf['price_declining'] = (inf_tf['price_change'] < -self.price_decline_threshold.value).astype(int)
                    
                    # 트렌드 방향 (하락 = 0, 상승 = 1)
                    inf_tf['trend'] = (inf_tf['close'] > inf_tf['ema_50']).astype(int)
                    
                    dataframe = merge_informative_pair(
                        dataframe, inf_tf, self.timeframe, self.informative_timeframe,
                        ffill=True, columns=['rsi', 'trend', 'price_change', 'price_declining', 'ema_50']
                    )
            except Exception as e:
                logger.warning(f"인포메이티브 데이터(4h) 로드 실패 ({pair}): {e}")
                # 기본값은 이미 설정되어 있음
        
        # === 더 넓은 타임프레임 데이터 (1d) ===
        wide_price_declining_col = f'wide_price_declining_{self.wide_timeframe}'
        wide_trend_col = f'wide_trend_{self.wide_timeframe}'
        
        # 기본값 먼저 설정
        dataframe[wide_price_declining_col] = 0
        dataframe[wide_trend_col] = 1
        
        if self.dp:
            try:
                wide_tf = self.dp.get_pair_dataframe(pair=pair, timeframe=self.wide_timeframe)
                if wide_tf is not None and not wide_tf.empty and len(wide_tf) > 50:  # 최소 데이터 확인
                    wide_tf['ema_50'] = ta.EMA(wide_tf['close'], timeperiod=50)
                    wide_tf['wide_trend'] = (wide_tf['close'] > wide_tf['ema_50']).astype(int)
                    
                    # 일봉에서 가격 하락 확인
                    wide_tf['wide_price_change'] = wide_tf['close'].pct_change(periods=5)
                    wide_tf['wide_price_declining'] = (wide_tf['wide_price_change'] < -0.10).astype(int)
                    
                    dataframe = merge_informative_pair(
                        dataframe, wide_tf, self.timeframe, self.wide_timeframe,
                        ffill=True, columns=['wide_trend', 'wide_price_change', 'wide_price_declining']
                    )
            except Exception as e:
                logger.warning(f"인포메이티브 데이터(1d) 로드 실패 ({pair}): {e}")
                # 기본값은 이미 설정되어 있음
        
        # === 트렌드 필터 ===
        dataframe['trend_up'] = (
            (dataframe['close'] > dataframe['ema_9']) &
            (dataframe['ema_9'] > dataframe['ema_21']) &
            (dataframe['ema_21'] > dataframe['ema_50'])
        ).astype(int)
        
        dataframe['trend_down'] = (
            (dataframe['close'] < dataframe['ema_9']) &
            (dataframe['ema_9'] < dataframe['ema_21']) &
            (dataframe['ema_21'] < dataframe['ema_50'])
        ).astype(int)
        
        return dataframe
    
    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        """
        진입 신호 생성
        
        핵심 패턴: 넓은 타임프레임에서 가격 하락 + OI 증가 + 시평갭 조건
        - 가격이 하락하는데 OI가 증가 = 역추세 반등 가능성
        - 시평갭(Mark Price vs Last Price)을 활용한 타이밍 포착
        """
        
        # === 롱 진입 조건 (역추세 반등 패턴) ===
        # 인포메이티브 컬럼명 (merge_informative_pair가 자동으로 타임프레임 추가)
        # populate_indicators에서 이미 생성되었지만, 안전을 위해 다시 확인
        price_declining_col = f'price_declining_{self.informative_timeframe}'
        wide_price_declining_col = f'wide_price_declining_{self.wide_timeframe}'
        wide_trend_col = f'wide_trend_{self.wide_timeframe}'
        rsi_inf_col = f'rsi_{self.informative_timeframe}'
        
        # 안전한 컬럼 접근 (없으면 기본값으로 채움)
        if price_declining_col not in dataframe.columns:
            dataframe[price_declining_col] = 0
        if wide_price_declining_col not in dataframe.columns:
            dataframe[wide_price_declining_col] = 0
        if wide_trend_col not in dataframe.columns:
            dataframe[wide_trend_col] = 1
        if rsi_inf_col not in dataframe.columns:
            dataframe[rsi_inf_col] = 50
        
        dataframe.loc[
            (
                # 기본 조건
                (dataframe['oi_valid'] == 1) &
                (dataframe['price_gap_valid'] == 1) &
                
                # === 핵심 패턴: 가격 하락 + OI 증가 ===
                # 넓은 타임프레임(4h)에서 가격 하락
                (dataframe[price_declining_col] == 1) &
                # 더 넓은 타임프레임(1d)에서도 하락 또는 약세
                (
                    (dataframe[wide_price_declining_col] == 1) |
                    (dataframe[wide_trend_col] == 0)
                ) &
                
                # === 시평갭 조건 ===
                # Last Price가 Mark Price보다 낮으면 프리미엄 상황 (긍정적)
                # 또는 갭이 적절한 범위 내에 있어야 함
                (dataframe['price_gap_percent'] >= -self.price_gap_max.value * 100) &
                (dataframe['price_gap_percent'] <= self.price_gap_max.value * 100) &
                
                # === 기술적 지표 (과매도 근처) ===
                # RSI가 낮은 위치에서 반등 가능성
                (dataframe['rsi_14'] < 45) &
                (dataframe['rsi_14'] > self.buy_rsi.value) &
                (dataframe['adx'] > 15) &
                
                # MACD 다이버전스 가능성
                (dataframe['macd'] > dataframe['macdsignal'] * 0.9) &
                
                # 볼륨 증가 (관심 증가)
                (dataframe['volume_ratio'] > self.volume_multiplier.value) &
                
                # 가격 위치 (하단 근처에서 반등 기대)
                (dataframe['bb_percent'] < 0.5) &
                (dataframe['bb_percent'] > 0.1) &
                
                # 넓은 타임프레임 RSI도 과매도 근처
                (dataframe[rsi_inf_col] < 50)
            ),
            ['enter_long', 'enter_tag']
        ] = (1, 'oi_reversal_long')
        
        # === 숏 진입 조건 (기존 로직 유지) ===
        trend_inf_col = f'trend_{self.informative_timeframe}'
        if trend_inf_col not in dataframe.columns:
            dataframe[trend_inf_col] = 0
        
        dataframe.loc[
            (
                # OI 조건
                (dataframe['oi_valid'] == 1) &
                
                # 기술적 지표
                (dataframe['rsi_14'] < self.sell_rsi.value) &
                (dataframe['rsi_14'] > 30) &
                (dataframe['adx'] > 20) &
                (dataframe['macd'] < dataframe['macdsignal']) &
                
                # 트렌드 확인
                (dataframe['trend_down'] == 1) &
                (dataframe[trend_inf_col] == 0) &
                
                # 볼륨 확인
                (dataframe['volume_ratio'] > self.volume_multiplier.value) &
                
                # 가격 위치
                (dataframe['bb_percent'] > 0.2) &
                (dataframe['bb_percent'] < 0.8)
            ),
            ['enter_short', 'enter_tag']
        ] = (1, 'oi_short')
        
        return dataframe
    
    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        """
        청산 신호 생성
        """
        
        # === 롱 청산 조건 ===
        dataframe.loc[
            (
                # RSI 과매수
                (dataframe['rsi_14'] > 75) |
                
                # MACD 반전
                (dataframe['macd'] < dataframe['macdsignal']) |
                
                # 트렌드 반전
                (dataframe['trend_down'] == 1) |
                
                # 볼린저 밴드 상단 돌파
                (dataframe['bb_percent'] > 0.95)
            ),
            ['exit_long', 'exit_tag']
        ] = (1, 'oi_exit_long')
        
        # === 숏 청산 조건 ===
        dataframe.loc[
            (
                # RSI 과매도
                (dataframe['rsi_14'] < 25) |
                
                # MACD 반전
                (dataframe['macd'] > dataframe['macdsignal']) |
                
                # 트렌드 반전
                (dataframe['trend_up'] == 1) |
                
                # 볼린저 밴드 하단 이탈
                (dataframe['bb_percent'] < 0.05)
            ),
            ['exit_short', 'exit_tag']
        ] = (1, 'oi_exit_short')
        
        return dataframe
    
    def custom_stoploss(self, pair: str, trade, current_time: datetime,
                       current_rate: float, current_profit: float, **kwargs) -> float:
        """
        ATR 기반 동적 스톱로스
        """
        dataframe, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
        if dataframe.empty:
            return self.stoploss
        
        last_candle = dataframe.iloc[-1].squeeze()
        
        # ATR 기반 동적 손절
        atr_stop_distance = last_candle['atr'] * 2.5
        
        if trade.is_short:
            stop_price = trade.open_rate + atr_stop_distance
            return (stop_price / current_rate) - 1
        else:
            stop_price = trade.open_rate - atr_stop_distance
            return (stop_price / current_rate) - 1
    
    def confirm_trade_entry(self, pair: str, order_type: str, amount: float, rate: float,
                           time_in_force: str, current_time: datetime, entry_tag: Optional[str],
                           side: str, **kwargs) -> bool:
        """
        실제 트레이딩 모드에서 OI 조건을 재확인합니다.
        백테스팅에서는 항상 True를 반환합니다.
        """
        # 백테스팅에서는 항상 True
        if not hasattr(self.dp, '_exchange') or not self.dp._exchange:
            return True
        
        exchange = self.dp._exchange
        
        # 바이낸스 선물 모드에서만 OI 확인
        if exchange.name == 'binance' and exchange.trading_mode.value == 'futures':
            oi_value = self.fetch_open_interest(pair)
            if oi_value is not None and oi_value < self.min_oi_value.value:
                logger.info(f"OI가 너무 낮아 진입 취소: {pair}, OI: {oi_value}")
                return False
        
        return True

