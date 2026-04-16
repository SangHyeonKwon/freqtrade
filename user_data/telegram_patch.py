"""
Freqtrade Telegram Patch

이 파일은 freqtrade의 텔레그램 클래스에 커스텀 커맨드를 추가하는 패치입니다.
"""

import logging
from functools import partial

logger = logging.getLogger(__name__)

def patch_telegram_commands(telegram_instance):
    """
    텔레그램 인스턴스에 커스텀 커맨드를 추가합니다.
    
    Args:
        telegram_instance: freqtrade의 텔레그램 인스턴스
    """
    try:
        from telegram.ext import CommandHandler
        
        logger.info("Patching telegram with custom commands...")
        
        # 커스텀 커맨드 핸들러들 정의
        custom_handlers = [
            CommandHandler("mystatus", partial(custom_status, telegram_instance)),
            CommandHandler("mybalance", partial(custom_balance, telegram_instance)),
            CommandHandler("mytrades", partial(custom_trades, telegram_instance)),
            CommandHandler("myhelp", partial(custom_help, telegram_instance)),
            CommandHandler("mystats", partial(custom_stats, telegram_instance)),
        ]
        
        # 핸들러들을 텔레그램 앱에 추가
        for handler in custom_handlers:
            telegram_instance._app.add_handler(handler)
        
        logger.info("Custom telegram commands patched successfully!")
        
    except Exception as e:
        logger.error(f"Error patching telegram commands: {e}")

async def custom_status(telegram_instance, update, context):
    """커스텀 상태 명령어: /mystatus"""
    try:
        # RPC를 통해 실제 상태 정보 가져오기
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

async def custom_balance(telegram_instance, update, context):
    """커스텀 잔고 명령어: /mybalance"""
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

async def custom_trades(telegram_instance, update, context):
    """커스텀 거래 내역 명령어: /mytrades"""
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

async def custom_stats(telegram_instance, update, context):
    """커스텀 통계 명령어: /mystats"""
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

async def custom_help(telegram_instance, update, context):
    """커스텀 도움말 명령어: /myhelp"""
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


















