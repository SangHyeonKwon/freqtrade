"""
Custom Telegram Commands Plugin for Freqtrade

이 플러그인은 freqtrade 텔레그램 봇에 커스텀 커맨드를 추가합니다.
"""

import logging
from typing import Any, Dict

from freqtrade.constants import Config
from freqtrade.rpc import RPC, RPCHandler
from freqtrade.rpc.rpc_types import RPCSendMsg

logger = logging.getLogger(__name__)


class CustomTelegramCommands(RPCHandler):
    """
    Custom Telegram Commands Plugin
    
    이 클래스는 텔레그램 봇에 커스텀 커맨드를 추가합니다.
    """

    def __init__(self, rpc: RPC, config: Config) -> None:
        """
        Initialize the custom telegram commands plugin
        """
        super().__init__(rpc, config)
        self._rpc = rpc
        self._config = config
        
        # 텔레그램 앱에 접근하기 위해 RPC 매니저에서 텔레그램 인스턴스를 가져옵니다
        self._telegram_instance = None
        
    def startup(self) -> None:
        """
        플러그인 시작 시 호출됩니다.
        여기서 커스텀 커맨드들을 등록합니다.
        """
        logger.info("Custom Telegram Commands plugin started")
        
        # RPC 매니저에서 텔레그램 인스턴스를 찾습니다
        if hasattr(self._rpc, '_freqtrade') and hasattr(self._rpc._freqtrade, 'rpc'):
            for module in self._rpc._freqtrade.rpc.registered_modules:
                if hasattr(module, '__class__') and 'Telegram' in module.__class__.__name__:
                    self._telegram_instance = module
                    self._register_custom_commands()
                    break
    
    def _register_custom_commands(self) -> None:
        """
        커스텀 커맨드들을 텔레그램 앱에 등록합니다.
        """
        if not self._telegram_instance or not hasattr(self._telegram_instance, '_app'):
            logger.warning("Telegram instance not found, custom commands not registered")
            return
            
        from telegram.ext import CommandHandler
        
        # 커스텀 커맨드 핸들러들을 등록
        custom_handlers = [
            CommandHandler("my_status", self._my_custom_status),
            CommandHandler("my_balance", self._my_custom_balance),
            CommandHandler("my_trades", self._my_custom_trades),
            CommandHandler("my_help", self._my_custom_help),
        ]
        
        for handler in custom_handlers:
            self._telegram_instance._app.add_handler(handler)
            
        logger.info("Custom telegram commands registered successfully")
    
    async def _my_custom_status(self, update, context) -> None:
        """
        커스텀 상태 명령어
        /my_status
        """
        try:
            # 기본 상태 정보 가져오기
            status = await self._rpc._rpc_status()
            
            # 커스텀 메시지 포맷팅
            custom_message = f"""
🤖 **내 커스텀 봇 상태** 🤖

📊 **현재 상태**: {status.get('state', 'Unknown')}
💰 **총 수익**: {status.get('profit_pct', 0):.2f}%
📈 **활성 거래**: {status.get('trade_count', 0)}개
⏰ **마지막 업데이트**: {status.get('last_update', 'Unknown')}

💡 **추가 정보**: 이것은 커스텀 커맨드입니다!
            """
            
            await update.message.reply_text(custom_message, parse_mode='Markdown')
            
        except Exception as e:
            logger.error(f"Error in custom status command: {e}")
            await update.message.reply_text("❌ 커스텀 상태를 가져오는 중 오류가 발생했습니다.")
    
    async def _my_custom_balance(self, update, context) -> None:
        """
        커스텀 잔고 명령어
        /my_balance
        """
        try:
            # 잔고 정보 가져오기
            balance = await self._rpc._rpc_balance()
            
            custom_message = f"""
💳 **내 커스텀 잔고 정보** 💳

💵 **총 잔고**: {balance.get('total', 0):.8f} BTC
💰 **사용 가능**: {balance.get('free', 0):.8f} BTC
🔒 **사용 중**: {balance.get('used', 0):.8f} BTC

📊 **상세 정보**:
"""
            
            # 각 코인별 잔고 정보 추가
            for currency, amount in balance.get('currencies', {}).items():
                if amount > 0:
                    custom_message += f"• {currency}: {amount:.8f}\n"
            
            custom_message += "\n💡 이것은 커스텀 잔고 명령어입니다!"
            
            await update.message.reply_text(custom_message, parse_mode='Markdown')
            
        except Exception as e:
            logger.error(f"Error in custom balance command: {e}")
            await update.message.reply_text("❌ 커스텀 잔고를 가져오는 중 오류가 발생했습니다.")
    
    async def _my_custom_trades(self, update, context) -> None:
        """
        커스텀 거래 내역 명령어
        /my_trades
        """
        try:
            # 거래 내역 가져오기
            trades = await self._rpc._rpc_trades()
            
            if not trades:
                await update.message.reply_text("📝 현재 활성 거래가 없습니다.")
                return
            
            custom_message = "📋 **내 커스텀 거래 내역** 📋\n\n"
            
            for trade in trades[:5]:  # 최근 5개만 표시
                custom_message += f"""
🔸 **{trade.get('pair', 'Unknown')}**
   💰 수익: {trade.get('profit_pct', 0):.2f}%
   📊 수량: {trade.get('amount', 0):.8f}
   ⏰ 시간: {trade.get('open_date', 'Unknown')}
   🎯 진입가: {trade.get('open_rate', 0):.8f}
"""
            
            if len(trades) > 5:
                custom_message += f"\n... 그리고 {len(trades) - 5}개 더"
            
            custom_message += "\n💡 이것은 커스텀 거래 내역 명령어입니다!"
            
            await update.message.reply_text(custom_message, parse_mode='Markdown')
            
        except Exception as e:
            logger.error(f"Error in custom trades command: {e}")
            await update.message.reply_text("❌ 커스텀 거래 내역을 가져오는 중 오류가 발생했습니다.")
    
    async def _my_custom_help(self, update, context) -> None:
        """
        커스텀 도움말 명령어
        /my_help
        """
        help_message = """
🆘 **내 커스텀 도움말** 🆘

**기본 명령어:**
• /my_status - 커스텀 봇 상태 확인
• /my_balance - 커스텀 잔고 정보
• /my_trades - 커스텀 거래 내역
• /my_help - 이 도움말 보기

**기존 freqtrade 명령어:**
• /status - 기본 봇 상태
• /profit - 수익 정보
• /balance - 기본 잔고
• /trades - 기본 거래 내역
• /start - 봇 시작
• /stop - 봇 중지

💡 **팁**: 커스텀 명령어는 더 자세한 정보와 이모지를 포함합니다!
        """
        
        await update.message.reply_text(help_message, parse_mode='Markdown')
    
    def cleanup(self) -> None:
        """
        플러그인 정리 시 호출됩니다.
        """
        logger.info("Custom Telegram Commands plugin cleaned up")
