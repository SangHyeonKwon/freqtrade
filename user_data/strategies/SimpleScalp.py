# --- 필수 import 구문 ---
import numpy as np
import pandas as pd
import logging
from pandas import DataFrame
from freqtrade.strategy import IStrategy
import talib.abstract as ta

logger = logging.getLogger(__name__)

class SimpleScalp(IStrategy):
    """
    간단한 단타 전략 - 시연용
    RSI 기반 빠른 진입/청산
    """
    
    # 기본 설정
    INTERFACE_VERSION = 3
    timeframe = '5m'  # 5분봉으로 빠른 거래
    can_short = False  # 롱만
    startup_candle_count = 50
    
    # 최대 오픈 포지션 수 증가 (시연용)
    max_open_trades = 5
    
    # 리스크 관리 (매우 타이트)
    stoploss = -0.02  # -2%
    trailing_stop = False
    
    # ROI (매우 빠른 수익 실현)
    minimal_roi = {
        "0": 0.003,   # 즉시 0.3%
        "2": 0.002,   # 2분 후 0.2%
        "5": 0.001,   # 5분 후 0.1%
        "10": 0.0005  # 10분 후 0.05%
    }

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        """
        기술적 지표 계산
        """
        # RSI
        dataframe['rsi'] = ta.RSI(dataframe, timeperiod=14)
        
        # MACD
        macd = ta.MACD(dataframe)
        dataframe['macd'] = macd['macd']
        dataframe['macdsignal'] = macd['macdsignal']
        dataframe['macdhist'] = macd['macdhist']
        
        # EMA (단기/장기)
        dataframe['ema_9'] = ta.EMA(dataframe, timeperiod=9)
        dataframe['ema_21'] = ta.EMA(dataframe, timeperiod=21)
        
        # 볼륨
        dataframe['volume_mean'] = dataframe['volume'].rolling(window=20).mean()
        dataframe['volume_ratio'] = dataframe['volume'] / dataframe['volume_mean']
        
        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        """
        매수 조건 (시연용 - 최대한 공격적)
        """
        # 현재 지표 값
        current_rsi = dataframe['rsi'].iloc[-1]
        current_volume_ratio = dataframe['volume_ratio'].iloc[-1]
        
        # 조건 확인
        rsi_ok = current_rsi < 75  # 더 완화
        volume_ok = current_volume_ratio > 0.1  # 더 완화
        
        # 디버깅용 로그
        logger.info(f"[{metadata['pair']}] RSI: {current_rsi:.2f} ({'OK' if rsi_ok else 'NO'}), Volume ratio: {current_volume_ratio:.2f} ({'OK' if volume_ok else 'NO'})")
        
        # 시연용: 조건을 최대한 완화 - 거의 항상 진입 가능하도록
        dataframe.loc[
            (
                # RSI 조건 매우 완화 (75 이하면 진입)
                (dataframe['rsi'] < 75) &
                # 볼륨 조건 매우 완화
                (dataframe['volume_ratio'] > 0.1)
            ),
            'enter_long'] = 1
        
        # 진입 신호 체크
        if dataframe['enter_long'].iloc[-1] == 1:
            logger.info(f"[{metadata['pair']}] ✅ ENTRY SIGNAL TRIGGERED!")

        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        """
        매도 조건 (빠른 청산) - 시연용으로 매우 완화
        """
        # 시연용: 빠르게 청산되도록 조건 매우 완화
        dataframe.loc[
            (
                # RSI 약간만 올라도 청산 (50 이상)
                (dataframe['rsi'] > 50) |
                # 또는 MACD 시그널 크로스다운
                (dataframe['macd'] < dataframe['macdsignal']) |
                # 또는 EMA 크로스다운 (단기 이평선이 장기 이평선 아래로)
                (dataframe['ema_9'] < dataframe['ema_21'])
            ),
            'exit_long'] = 1

        return dataframe

