"""
Custom Telegram Extension for Freqtrade

이 파일은 freqtrade의 텔레그램 기능을 확장하여 커스텀 커맨드를 추가합니다.
"""

import logging
from typing import Any, Dict

logger = logging.getLogger(__name__)


class CustomTelegramExtension:
    """
    커스텀 텔레그램 확장 클래스
    
    이 클래스는 freqtrade의 텔레그램 기능에 커스텀 커맨드를 추가합니다.
    """
    
    def __init__(self, freqtrade_bot):
        """
        freqtrade 봇 인스턴스를 받아서 텔레그램 확장을 초기화합니다.
        """
        self.freqtrade = freqtrade_bot
        self._setup_custom_commands()
    
    def _setup_custom_commands(self):
        """
        커스텀 커맨드들을 설정합니다.
        """
        logger.info("Setting up custom telegram commands...")
        
        # 텔레그램 인스턴스 찾기
        telegram_instance = None
        if hasattr(self.freqtrade, 'rpc') and hasattr(self.freqtrade.rpc, 'registered_modules'):
            for module in self.freqtrade.rpc.registered_modules:
                if hasattr(module, '__class__') and 'Telegram' in module.__class__.__name__:
                    telegram_instance = module
                    break
        
        if telegram_instance:
            self._add_custom_handlers(telegram_instance)
        else:
            logger.warning("Telegram instance not found")
    
    def _add_custom_handlers(self, telegram_instance):
        """
        텔레그램 인스턴스에 커스텀 핸들러들을 추가합니다.
        """
        from telegram.ext import CommandHandler
        from functools import partial
        
        # 커스텀 커맨드 핸들러들 생성
        custom_handlers = [
            CommandHandler("mystatus", partial(self._custom_status, telegram_instance)),
            CommandHandler("mybalance", partial(self._custom_balance, telegram_instance)),
            CommandHandler("mytrades", partial(self._custom_trades, telegram_instance)),
            CommandHandler("myhelp", partial(self._custom_help, telegram_instance)),
            CommandHandler("mystats", partial(self._custom_stats, telegram_instance)),
        ]
        
        # 핸들러들을 텔레그램 앱에 추가
        for handler in custom_handlers:
            telegram_instance._app.add_handler(handler)
        
        logger.info("Custom telegram handlers added successfully")
    
    async def _custom_status(self, telegram_instance, update, context):
        """
        커스텀 상태 명령어: /mystatus
        """
        try:
            # RPC를 통해 상태 정보 가져오기
            status = await telegram_instance._rpc._rpc_status()
            
            message = f"""
🤖 **내 커스텀 봇 상태** 🤖

📊 **상태**: {status.get('state', 'Unknown')}
💰 **총 수익률**: {status.get('profit_pct', 0):.2f}%
📈 **활성 거래**: {status.get('trade_count', 0)}개
⏰ **마지막 업데이트**: {status.get('last_update', 'Unknown')}

💡 **커스텀 기능**: 이것은 나만의 커스텀 명령어입니다!
            """
            
            await update.message.reply_text(message, parse_mode='Markdown')
            
        except Exception as e:
            logger.error(f"Error in custom status: {e}")
            await update.message.reply_text("❌ 상태를 가져오는 중 오류가 발생했습니다.")
    
    async def _custom_balance(self, telegram_instance, update, context):
        """
        커스텀 잔고 명령어: /mybalance
        """
        try:
            balance = await telegram_instance._rpc._rpc_balance()
            
            message = f"""
💳 **내 커스텀 잔고** 💳

💵 **총 잔고**: {balance.get('total', 0):.8f} BTC
💰 **사용 가능**: {balance.get('free', 0):.8f} BTC
🔒 **사용 중**: {balance.get('used', 0):.8f} BTC

📊 **상세 잔고**:
"""
            
            # 각 코인별 잔고 표시
            for currency, amount in balance.get('currencies', {}).items():
                if amount > 0.00000001:  # 매우 작은 금액은 제외
                    message += f"• {currency}: {amount:.8f}\n"
            
            message += "\n💡 **커스텀 기능**: 더 자세한 잔고 정보입니다!"
            
            await update.message.reply_text(message, parse_mode='Markdown')
            
        except Exception as e:
            logger.error(f"Error in custom balance: {e}")
            await update.message.reply_text("❌ 잔고를 가져오는 중 오류가 발생했습니다.")
    
    async def _custom_trades(self, telegram_instance, update, context):
        """
        커스텀 거래 내역 명령어: /mytrades
        """
        try:
            trades = await telegram_instance._rpc._rpc_trades()
            
            if not trades:
                await update.message.reply_text("📝 현재 활성 거래가 없습니다.")
                return
            
            message = "📋 **내 커스텀 거래 내역** 📋\n\n"
            
            # 최근 5개 거래만 표시
            for i, trade in enumerate(trades[:5], 1):
                profit_emoji = "📈" if trade.get('profit_pct', 0) > 0 else "📉"
                message += f"""
{profit_emoji} **거래 #{i}**: {trade.get('pair', 'Unknown')}
   💰 수익률: {trade.get('profit_pct', 0):.2f}%
   📊 수량: {trade.get('amount', 0):.8f}
   ⏰ 진입시간: {trade.get('open_date', 'Unknown')}
   🎯 진입가: {trade.get('open_rate', 0):.8f}
   🛑 손절가: {trade.get('stop_loss_abs', 0):.8f}
"""
            
            if len(trades) > 5:
                message += f"\n... 그리고 {len(trades) - 5}개 더"
            
            message += "\n💡 **커스텀 기능**: 더 자세한 거래 정보입니다!"
            
            await update.message.reply_text(message, parse_mode='Markdown')
            
        except Exception as e:
            logger.error(f"Error in custom trades: {e}")
            await update.message.reply_text("❌ 거래 내역을 가져오는 중 오류가 발생했습니다.")
    
    async def _custom_stats(self, telegram_instance, update, context):
        """
        커스텀 통계 명령어: /mystats
        """
        try:
            stats = await telegram_instance._rpc._rpc_stats()
            
            message = f"""
📊 **내 커스텀 통계** 📊

🎯 **총 거래**: {stats.get('total_trades', 0)}개
✅ **승리 거래**: {stats.get('wins', 0)}개
❌ **패배 거래**: {stats.get('losses', 0)}개
📈 **승률**: {stats.get('win_rate', 0):.1f}%

💰 **수익 정보**:
   • 총 수익: {stats.get('profit_total', 0):.2f}%
   • 평균 수익: {stats.get('profit_mean', 0):.2f}%
   • 최대 수익: {stats.get('profit_total_abs', 0):.8f} BTC

⏰ **거래 기간**:
   • 첫 거래: {stats.get('first_trade_date', 'Unknown')}
   • 마지막 거래: {stats.get('last_trade_date', 'Unknown')}

💡 **커스텀 기능**: 상세한 통계 정보입니다!
            """
            
            await update.message.reply_text(message, parse_mode='Markdown')
            
        except Exception as e:
            logger.error(f"Error in custom stats: {e}")
            await update.message.reply_text("❌ 통계를 가져오는 중 오류가 발생했습니다.")
    
    async def _custom_help(self, telegram_instance, update, context):
        """
        커스텀 도움말 명령어: /myhelp
        """
        help_message = """
🆘 **내 커스텀 도움말** 🆘

**🎯 커스텀 명령어:**
• `/mystatus` - 커스텀 봇 상태 확인
• `/mybalance` - 커스텀 잔고 정보  
• `/mytrades` - 커스텀 거래 내역
• `/mystats` - 커스텀 통계 정보
• `/myhelp` - 이 도움말 보기

**🔧 기본 freqtrade 명령어:**
• `/status` - 기본 봇 상태
• `/profit` - 수익 정보
• `/balance` - 기본 잔고
• `/trades` - 기본 거래 내역
• `/start` - 봇 시작
• `/stop` - 봇 중지
• `/help` - 기본 도움말

**💡 차이점:**
커스텀 명령어는 더 자세한 정보와 이모지, 
한국어 메시지를 제공합니다!

**🚀 사용법:**
텔레그램에서 `/mystatus` 같은 명령어를 입력하면
커스텀 정보를 받을 수 있습니다.
        """
        
        await update.message.reply_text(help_message, parse_mode='Markdown')
