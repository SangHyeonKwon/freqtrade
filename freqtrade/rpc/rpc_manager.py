"""
This module contains class to manage RPC communications (Telegram, API, ...)
"""

import logging
from collections import deque

from freqtrade.constants import Config
from freqtrade.enums import NO_ECHO_MESSAGES, RPCMessageType
from freqtrade.rpc import RPC, RPCHandler
from freqtrade.rpc.rpc_types import RPCSendMsg


logger = logging.getLogger(__name__)


def format_roi(roi_dict: dict) -> str:
    """
    Format ROI dictionary to show minimum ROI value (key '0' or highest value)
    Example: {'0': 0.04, '15': 0.06} -> "4.0%"
    """
    if not roi_dict or not isinstance(roi_dict, dict):
        return str(roi_dict)
    
    # Get ROI value for immediate exit (key '0') if exists, otherwise highest value
    if "0" in roi_dict:
        roi_value = roi_dict["0"]
    else:
        roi_value = max(roi_dict.values())
    
    roi_percent = roi_value * 100
    
    return f"{roi_percent:.1f}%"


def format_state_korean(state_str: str) -> str:
    """상태를 한국어로 변환"""
    state_map = {
        "running": "실행 중",
        "paused": "일시 정지",
        "stopped": "중지됨",
        "reload_config": "설정 재로드 중",
    }
    return state_map.get(state_str.lower(), state_str)


class RPCManager:
    """
    Class to manage RPC objects (Telegram, API, ...)
    """

    def __init__(self, freqtrade) -> None:
        """Initializes all enabled rpc modules"""
        self.registered_modules: list[RPCHandler] = []
        self._rpc = RPC(freqtrade)
        config = freqtrade.config
        # Enable telegram
        if config.get("telegram", {}).get("enabled", False):
            logger.info("Enabling rpc.telegram ...")
            from freqtrade.rpc.telegram import Telegram

            self.registered_modules.append(Telegram(self._rpc, config))

        # Enable discord
        if config.get("discord", {}).get("enabled", False):
            logger.info("Enabling rpc.discord ...")
            from freqtrade.rpc.discord import Discord

            self.registered_modules.append(Discord(self._rpc, config))

        # Enable Webhook
        if config.get("webhook", {}).get("enabled", False):
            logger.info("Enabling rpc.webhook ...")
            from freqtrade.rpc.webhook import Webhook

            self.registered_modules.append(Webhook(self._rpc, config))

        # Enable local rest api server for cmd line control
        if config.get("api_server", {}).get("enabled", False):
            logger.info("Enabling rpc.api_server")
            from freqtrade.rpc.api_server import ApiServer

            apiserver = ApiServer(config)
            apiserver.add_rpc_handler(self._rpc)
            self.registered_modules.append(apiserver)

    def cleanup(self) -> None:
        """Stops all enabled rpc modules"""
        logger.info("Cleaning up rpc modules ...")
        while self.registered_modules:
            mod = self.registered_modules.pop()
            logger.info("Cleaning up rpc.%s ...", mod.name)
            mod.cleanup()
            del mod

    def send_msg(self, msg: RPCSendMsg) -> None:
        """
        Send given message to all registered rpc modules.
        A message consists of one or more key value pairs of strings.
        e.g.:
        {
            'status': 'stopping bot'
        }
        """
        if msg.get("type") not in NO_ECHO_MESSAGES:
            logger.info("Sending rpc message: %s", msg)
        for mod in self.registered_modules:
            logger.debug("Forwarding message to rpc.%s", mod.name)
            try:
                mod.send_msg(msg)
            except NotImplementedError:
                logger.error(f"Message type '{msg['type']}' not implemented by handler {mod.name}.")
            except Exception:
                logger.exception("Exception occurred within RPC module %s", mod.name)

    def process_msg_queue(self, queue: deque) -> None:
        """
        Process all messages in the queue.
        """
        while queue:
            msg = queue.popleft()
            logger.info("Sending rpc strategy_msg: %s", msg)
            for mod in self.registered_modules:
                if mod._config.get(mod.name, {}).get("allow_custom_messages", False):
                    mod.send_msg(
                        {
                            "type": RPCMessageType.STRATEGY_MSG,
                            "msg": msg,
                        }
                    )

    def startup_messages(self, config: Config, pairlist, protections, state: str | None = None) -> None:
        stake_currency = config["stake_currency"]
        stake_amount = config["stake_amount"]
        minimal_roi = config["minimal_roi"]
        stoploss = config["stoploss"]
        trailing_stop = config["trailing_stop"]
        timeframe = config["timeframe"]
        exchange_raw_name = config["exchange"]["name"]
        # Pretty name for display (keep config value intact for ccxt)
        exchange_name = (
            "Binance"
            if exchange_raw_name.lower() in ("binance", "binanceusdm", "binanceusdtm")
            else exchange_raw_name
        )
        strategy_name = config.get("strategy", "")
        pos_adjust_enabled = "On" if config["position_adjustment_enable"] else "Off"
        
        # 통합된 구조화된 메시지 생성
        message_parts = []
        message_parts.append("🤖 *봇 시작 알림*")
        message_parts.append("━━━━━━━━━━━━━━━━")
        message_parts.append("")
        message_parts.append("📊 *설정 정보*")
        message_parts.append(f"• 거래소: `{exchange_name}`")
        message_parts.append(f"• 전략: `{strategy_name}`")
        message_parts.append(f"• 타임프레임: `{timeframe}`")
        message_parts.append(f"• 포지션당 마진: `{stake_amount} {stake_currency}`")
        message_parts.append(f"• 최소 수익률: `{format_roi(minimal_roi)}`")
        message_parts.append(f"• {'후행 ' if trailing_stop else ''}스탑로스: `{stoploss}`")
        message_parts.append(f"• 포지션 조정: `{pos_adjust_enabled}`")
        message_parts.append("")
        message_parts.append("🔍 *페어리스트*")
        message_parts.append(f"{stake_currency} 페어를 검색하여 매수 및 매도합니다")
        
        if len(protections.name_list) > 0:
            prots = "\n".join([p for prot in protections.short_desc() for k, p in prot.items()])
            message_parts.append("")
            message_parts.append("🛡️ *보호 기능*")
            message_parts.append(prots)
        
        if config["dry_run"]:
            message_parts.append("")
            message_parts.append("⚠️ *주의*")
            message_parts.append("드라이런이 활성화되어 있습니다.")
            message_parts.append("모든 거래는 시뮬레이션됩니다.")
        
        message_parts.append("")
        message_parts.append("━━━━━━━━━━━━━━━━")
        
        # 하나의 통합 메시지로 전송
        self.send_msg(
            {
                "type": RPCMessageType.STARTUP,
                "status": "\n".join(message_parts),
            }
        )
