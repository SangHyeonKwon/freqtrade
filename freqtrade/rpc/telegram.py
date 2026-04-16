# pragma pylint: disable=unused-argument, unused-variable, protected-access, invalid-name

"""
This module manage Telegram communication
"""

import asyncio
import json
import logging
import re
from collections.abc import Callable, Coroutine
from copy import deepcopy
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from functools import partial, wraps
from html import escape
from itertools import chain
from math import isnan
from urllib.parse import quote_plus
from threading import Thread
from typing import Any, Literal

from tabulate import tabulate
from telegram import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
    Update,
)
from telegram.constants import MessageLimit, ParseMode
from telegram.error import BadRequest, NetworkError, TelegramError
from telegram.ext import Application, CallbackContext, CallbackQueryHandler, CommandHandler, MessageHandler, filters
from telegram.helpers import escape_markdown

from freqtrade.__init__ import __version__
from freqtrade.constants import DUST_PER_COIN, Config
from freqtrade.enums import MarketDirection, RPCMessageType, SignalDirection, TradingMode
from freqtrade.exceptions import OperationalException
from freqtrade.misc import chunks, plural
from freqtrade.persistence import Trade
from freqtrade.rpc import RPC, RPCException, RPCHandler
from freqtrade.rpc.rpc_types import RPCEntryMsg, RPCExitMsg, RPCOrderMsg, RPCSendMsg
from freqtrade.util import (
    dt_from_ts,
    dt_humanize_delta,
    fmt_coin,
    fmt_coin2,
    format_date,
    round_value,
)


MAX_MESSAGE_LENGTH = MessageLimit.MAX_TEXT_LENGTH


logger = logging.getLogger(__name__)

logger.debug("Included module rpc.telegram ...")


def safe_async_db(func: Callable[..., Any]):
    """
    Decorator to safely handle sessions when switching async context
    :param func: function to decorate
    :return: decorated function
    """

    @wraps(func)
    def wrapper(*args, **kwargs):
        """Decorator logic"""
        try:
            return func(*args, **kwargs)
        finally:
            Trade.session.remove()

    return wrapper


@dataclass
class TimeunitMappings:
    header: str
    message: str
    message2: str
    callback: str
    default: int
    dateformat: str


def authorized_only(command_handler: Callable[..., Coroutine[Any, Any, None]]):
    """
    Decorator to check if the message comes from the correct chat_id
    can only be used with Telegram Class to decorate instance methods.
    :param command_handler: Telegram CommandHandler
    :return: decorated function
    """

    @wraps(command_handler)
    async def wrapper(self, *args, **kwargs) -> None:
        """Decorator logic"""
        update = kwargs.get("update") or args[0]

        # Reject unauthorized messages
        message: Message = (
            update.message if update.callback_query is None else update.callback_query.message
        )
        cchat_id: int = int(message.chat_id)
        ctopic_id: int | None = message.message_thread_id
        from_user_id: str = str(update.effective_user.id if update.effective_user else "")

        chat_id = int(self._config["telegram"]["chat_id"])
        if cchat_id != chat_id:
            logger.info(f"Rejected unauthorized message from: {cchat_id}")
            return None
        if (topic_id := self._config["telegram"].get("topic_id")) is not None:
            if str(ctopic_id) != topic_id:
                # This can be quite common in multi-topic environments.
                logger.debug(f"Rejected message from wrong channel: {cchat_id}, {ctopic_id}")
                return None

        authorized = self._config["telegram"].get("authorized_users", None)
        if authorized is not None and from_user_id not in authorized:
            logger.info(f"Unauthorized user tried to control the bot: {from_user_id}")
            return None
        # Rollback session to avoid getting data stored in a transaction.
        Trade.rollback()
        logger.debug("Executing handler: %s for chat_id: %s", command_handler.__name__, chat_id)
        try:
            return await command_handler(self, *args, **kwargs)
        except RPCException as e:
            await self._send_msg(str(e))
        except BaseException:
            logger.exception("Exception occurred within Telegram module")
        finally:
            Trade.session.remove()

    return wrapper


class Telegram(RPCHandler):
    """This class handles all telegram communication"""

    def __init__(self, rpc: RPC, config: Config) -> None:
        """
        Init the Telegram call, and init the super class RPCHandler
        :param rpc: instance of RPC Helper class
        :param config: Configuration object
        :return: None
        """
        super().__init__(rpc, config)

        self._app: Application
        self._loop: asyncio.AbstractEventLoop
        # Backtesting state management: {user_id: {'step': 'strategy'|'start_date'|'end_date', 'strategy': str, 'start_date': str, 'end_date': str, 'messages': [message_id]}}
        self._backtesting_state: dict[int, dict[str, Any]] = {}
        self._init_keyboard()
        self._start_thread()

    def _start_thread(self):
        """
        Creates and starts the polling thread
        """
        self._thread = Thread(target=self._init, name="FTTelegram")
        self._thread.start()

    def _init_keyboard(self) -> None:
        """
        Validates the keyboard configuration from telegram config
        section.
        """
        self._keyboard: list[list[str | KeyboardButton]] = [
            ["/pnl", "/backtesting", "/help"],
            ["/position", "/signal"],
        ]
        # do not allow commands with mandatory arguments and critical cmds
        # TODO: DRY! - its not good to list all valid cmds here. But otherwise
        #       this needs refactoring of the whole telegram module (same
        #       problem in _help()).
        valid_keys: list[str] = [
            r"/start$",
            r"/pause$",
            r"/stop$",
            r"/status$",
            r"/status table$",
            r"/trades$",
            r"/performance$",
            r"/buys",
            r"/entries",
            r"/sells",
            r"/exits",
            r"/mix_tags",
            r"/daily$",
            r"/daily \d+$",
            r"/profit([_ ]long|[_ ]short)?$",
            r"/profit([_ ]long|[_ ]short)? \d+$",
            r"/stats$",
            r"/count$",
            r"/locks$",
            r"/balance$",
            r"/stopbuy$",
            r"/stopentry$",
            r"/reload_config$",
            r"/show_config$",
            r"/logs$",
            r"/whitelist$",
            r"/whitelist(\ssorted|\sbaseonly)+$",
            r"/blacklist$",
            r"/bl_delete$",
            r"/weekly$",
            r"/weekly \d+$",
            r"/monthly$",
            r"/monthly \d+$",
            r"/forcebuy$",
            r"/forcelong$",
            r"/forceshort$",
            r"/forcesell$",
            r"/forceexit$",
            r"/health$",
            r"/help$",
            r"/version$",
            r"/marketdir (long|short|even|none)$",
            r"/marketdir$",
            r"/pnl$",
            r"/backtesting$",
            r"/position$",
            r"/signal$",
        ]
        # Create keys for generation
        valid_keys_print = [k.replace("$", "") for k in valid_keys]

        # custom keyboard specified in config.json
        cust_keyboard = self._config["telegram"].get("keyboard", [])
        if cust_keyboard:
            combined = "(" + ")|(".join(valid_keys) + ")"
            # check for valid shortcuts
            invalid_keys = [
                b for b in chain.from_iterable(cust_keyboard) if not re.match(combined, b)
            ]
            if len(invalid_keys):
                err_msg = (
                    "config.telegram.keyboard: Invalid commands for "
                    f"custom Telegram keyboard: {invalid_keys}"
                    f"\nvalid commands are: {valid_keys_print}"
                )
                raise OperationalException(err_msg)
            else:
                self._keyboard = cust_keyboard
                logger.info(f"using custom keyboard from config.json: {self._keyboard}")

    def _init_telegram_app(self):
        return Application.builder().token(self._config["telegram"]["token"]).build()

    def _init(self) -> None:
        """
        Initializes this module with the given config,
        registers all known command handlers
        and starts polling for message updates
        Runs in a separate thread.
        """
        try:
            self._loop = asyncio.get_running_loop()
        except RuntimeError:
            self._loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self._loop)

        self._app = self._init_telegram_app()

        # Register command handler and start telegram message polling
        handles = [
            CommandHandler("status", self._status),
            CommandHandler("profit", self._profit),
            CommandHandler("balance", self._balance),
            CommandHandler("start", self._start),
            CommandHandler("stop", self._stop),
            CommandHandler(["forcesell", "forceexit", "fx"], self._force_exit),
            CommandHandler(
                ["forcebuy", "forcelong"],
                partial(self._force_enter, order_side=SignalDirection.LONG),
            ),
            CommandHandler(
                "forceshort", partial(self._force_enter, order_side=SignalDirection.SHORT)
            ),
            CommandHandler("reload_trade", self._reload_trade_from_exchange),
            CommandHandler("trades", self._trades),
            CommandHandler("delete", self._delete_trade),
            CommandHandler(["coo", "cancel_open_order"], self._cancel_open_order),
            CommandHandler("performance", self._performance),
            CommandHandler(["buys", "entries"], self._enter_tag_performance),
            CommandHandler(["sells", "exits"], self._exit_reason_performance),
            CommandHandler("mix_tags", self._mix_tag_performance),
            CommandHandler("stats", self._stats),
            CommandHandler("daily", self._daily),
            CommandHandler("weekly", self._weekly),
            CommandHandler("monthly", self._monthly),
            CommandHandler("count", self._count),
            CommandHandler("locks", self._locks),
            CommandHandler(["unlock", "delete_locks"], self._delete_locks),
            CommandHandler(["reload_config", "reload_conf"], self._reload_config),
            CommandHandler(["show_config", "show_conf"], self._show_config),
            CommandHandler(["stopbuy", "stopentry", "pause"], self._pause),
            CommandHandler("whitelist", self._whitelist),
            CommandHandler("blacklist", self._blacklist),
            CommandHandler(["blacklist_delete", "bl_delete"], self._blacklist_delete),
            CommandHandler("logs", self._logs),
            CommandHandler("health", self._health),
            CommandHandler("help", self._help),
            CommandHandler("version", self._version),
            CommandHandler("marketdir", self._changemarketdir),
            CommandHandler("order", self._order),
            CommandHandler("list_custom_data", self._list_custom_data),
            CommandHandler("tg_info", self._tg_info),
            CommandHandler("profit_long", self._profit_long),
            CommandHandler("profit_short", self._profit_short),
            CommandHandler("backtesting", self._custom_backtesting),
            CommandHandler("pnl", self._custom_pnl),
            CommandHandler("position", self._position),
            CommandHandler("signal", self._signal),
        ]
        callbacks = [
            CallbackQueryHandler(self._status_table, pattern="update_status_table"),
            CallbackQueryHandler(self._daily, pattern="update_daily"),
            CallbackQueryHandler(self._weekly, pattern="update_weekly"),
            CallbackQueryHandler(self._monthly, pattern="update_monthly"),
            CallbackQueryHandler(self._profit_long, pattern="update_profit_long"),
            CallbackQueryHandler(self._profit_short, pattern="update_profit_short"),
            CallbackQueryHandler(self._profit, pattern=r"update_profit$"),
            CallbackQueryHandler(self._balance, pattern="update_balance"),
            CallbackQueryHandler(self._performance, pattern="update_performance"),
            CallbackQueryHandler(
                self._enter_tag_performance, pattern="update_enter_tag_performance"
            ),
            CallbackQueryHandler(
                self._exit_reason_performance, pattern="update_exit_reason_performance"
            ),
            CallbackQueryHandler(self._mix_tag_performance, pattern="update_mix_tag_performance"),
            CallbackQueryHandler(self._count, pattern="update_count"),
            CallbackQueryHandler(self._force_exit_inline, pattern=r"force_exit__\S+"),
            CallbackQueryHandler(self._force_enter_inline, pattern=r"force_enter__\S+"),
            CallbackQueryHandler(self._backtesting_strategy_callback, pattern=r"^backtest_(strategy__.*|cancel)$"),
        ]
        # Message handler for backtesting date input
        message_handlers = [
            MessageHandler(filters.TEXT & ~filters.COMMAND, self._backtesting_message_handler),
        ]
        
        for handle in handles:
            self._app.add_handler(handle)

        for callback in callbacks:
            self._app.add_handler(callback)
        
        for msg_handler in message_handlers:
            self._app.add_handler(msg_handler)

        logger.info(
            "rpc.telegram is listening for following commands: %s",
            [[x for x in sorted(h.commands)] for h in handles],
        )
        self._loop.run_until_complete(self._startup_telegram())

    async def _startup_telegram(self) -> None:
        retries = 3
        attempt = 0
        while attempt < retries:
            try:
                await self._app.initialize()
                await self._app.start()
                break
            except Exception as ex:
                logger.error(
                    "Error starting Telegram bot (attempt %d/%d): %s", attempt + 1, retries, ex
                )
                attempt += 1
                if attempt == retries:
                    logger.warning("Telegram init failed.")
                    return
                await asyncio.sleep(2)
        if self._app.updater:
            await self._app.updater.start_polling(
                bootstrap_retries=10,
                timeout=20,
                drop_pending_updates=True,
            )
            while True:
                await asyncio.sleep(10)
                if not self._app.updater.running:
                    break

    async def _cleanup_telegram(self) -> None:
        if self._app.updater:
            await self._app.updater.stop()
        await self._app.stop()
        await self._app.shutdown()

    def cleanup(self) -> None:
        """
        Stops all running telegram threads.
        :return: None
        """
        # This can take up to `timeout` from the call to `start_polling`.
        asyncio.run_coroutine_threadsafe(self._cleanup_telegram(), self._loop)
        self._thread.join()

    def _exchange_from_msg(self, msg: RPCOrderMsg) -> str:
        """
        Extracts the exchange name from the given message.
        :param msg: The message to extract the exchange name from.
        :return: The exchange name.
        """
        return f"{msg['exchange']}{' (dry)' if self._config['dry_run'] else ''}"

    def _add_analyzed_candle(self, pair: str) -> str:
        candle_val = (
            self._config["telegram"].get("notification_settings", {}).get("show_candle", "off")
        )
        if candle_val != "off":
            if candle_val == "ohlc":
                analyzed_df, _ = self._rpc._freqtrade.dataprovider.get_analyzed_dataframe(
                    pair, self._config["timeframe"]
                )
                candle = analyzed_df.iloc[-1].squeeze() if len(analyzed_df) > 0 else None
                if candle is not None:
                    return (
                        f"*Candle OHLC*: `{candle['open']}, {candle['high']}, "
                        f"{candle['low']}, {candle['close']}`\n"
                    )

        return ""

    def _format_entry_msg(self, msg: RPCEntryMsg) -> str:
        is_fill = msg["type"] in [RPCMessageType.ENTRY_FILL]
        emoji = "\N{CHECK MARK}" if is_fill else "\N{LARGE BLUE CIRCLE}"

        terminology = {
            "1_enter": "신규 거래",
            "1_entered": "신규 거래 체결",
            "x_enter": "포지션 증가",
            "x_entered": "포지션 증가 체결",
        }

        key = f"{'x' if msg['sub_trade'] else '1'}_{'entered' if is_fill else 'enter'}"
        wording = terminology[key]

        message = (
            f"{emoji} *{self._exchange_from_msg(msg)}:*"
            f" {wording} (#{msg['trade_id']})\n"
            f"*페어:* `{msg['pair']}`\n"
        )
        message += self._add_analyzed_candle(msg["pair"])
        message += f"*진입 태그:* `{msg['enter_tag']}`\n" if msg.get("enter_tag") else ""
        message += f"*수량:* `{round_value(msg['amount'], 8)}`\n"
        direction_map = {"long": "롱", "short": "숏"}
        direction_kr = direction_map.get(msg['direction'].lower(), msg['direction'])
        message += f"*방향:* `{direction_kr}"
        if msg.get("leverage") and msg.get("leverage", 1.0) != 1.0:
            message += f" ({msg['leverage']:.3g}배)"
        message += "`\n"
        message += f"*진입가:* `{fmt_coin2(msg['open_rate'], msg['quote_currency'])}`\n"
        if msg["type"] == RPCMessageType.ENTRY and msg["current_rate"]:
            message += (
                f"*현재가:* `{fmt_coin2(msg['current_rate'], msg['quote_currency'])}`\n"
            )

        profit_fiat_extra = self.__format_profit_fiat(msg, "stake_amount")  # type: ignore
        total = fmt_coin(msg["stake_amount"], msg["quote_currency"])

        message += f"*{'신규 ' if msg['sub_trade'] else ''}총액:* `{total}{profit_fiat_extra}`"

        return message

    def _format_exit_msg(self, msg: RPCExitMsg) -> str:
        duration = msg["close_date"].replace(microsecond=0) - msg["open_date"].replace(
            microsecond=0
        )
        duration_min = duration.total_seconds() / 60

        leverage_text = (
            f" ({msg['leverage']:.3g}배)"
            if msg.get("leverage") and msg.get("leverage", 1.0) != 1.0
            else ""
        )

        profit_fiat_extra = self.__format_profit_fiat(msg, "profit_amount")

        profit_extra = (
            f" ({msg['gain']}: {fmt_coin(msg['profit_amount'], msg['quote_currency'])}"
            f"{profit_fiat_extra})"
        )

        is_fill = msg["type"] == RPCMessageType.EXIT_FILL
        is_sub_trade = msg.get("sub_trade")
        is_sub_profit = msg["profit_amount"] != msg.get("cumulative_profit")
        is_final_exit = msg.get("is_final_exit", False) and is_sub_profit
        profit_prefix = "부분 " if is_sub_trade else ""
        cp_extra = ""
        exit_wording = "청산 완료" if is_fill else "청산 중"
        if is_sub_trade or is_final_exit:
            cp_fiat = self.__format_profit_fiat(msg, "cumulative_profit")

            if is_final_exit:
                profit_prefix = "부분 "
                cp_extra = (
                    f"*최종 수익:* `{msg['final_profit_ratio']:.2%} "
                    f"({msg['cumulative_profit']:.8f} {msg['quote_currency']}{cp_fiat})`\n"
                )
            else:
                exit_wording = f"부분 {exit_wording.lower()}"
                if msg["cumulative_profit"]:
                    cp_extra = (
                        f"*누적 수익:* `"
                        f"{fmt_coin(msg['cumulative_profit'], msg['stake_currency'])}{cp_fiat}`\n"
                    )
        enter_tag = f"*진입 태그:* `{msg['enter_tag']}`\n" if msg.get("enter_tag") else ""
        profit_label = f"{profit_prefix}수익" if is_fill else f"미실현 {profit_prefix}수익"
        direction_map = {"long": "롱", "short": "숏"}
        direction_kr = direction_map.get(msg['direction'].lower(), msg['direction'])
        message = (
            f"{self._get_exit_emoji(msg)} *{self._exchange_from_msg(msg)}:* "
            f"{exit_wording} {msg['pair']} (#{msg['trade_id']})\n"
            f"{self._add_analyzed_candle(msg['pair'])}"
            f"*{profit_label}:* "
            f"`{msg['profit_ratio']:.2%}{profit_extra}`\n"
            f"{cp_extra}"
            f"{enter_tag}"
            f"*청산 사유:* `{msg['exit_reason']}`\n"
            f"*방향:* `{direction_kr}"
            f"{leverage_text}`\n"
            f"*수량:* `{round_value(msg['amount'], 8)}`\n"
            f"*진입가:* `{fmt_coin2(msg['open_rate'], msg['quote_currency'])}`\n"
        )
        if msg["type"] == RPCMessageType.EXIT and msg["current_rate"]:
            message += (
                f"*현재가:* `{fmt_coin2(msg['current_rate'], msg['quote_currency'])}`\n"
            )
            if msg["order_rate"]:
                message += f"*청산가:* `{fmt_coin2(msg['order_rate'], msg['quote_currency'])}`"
        elif msg["type"] == RPCMessageType.EXIT_FILL:
            message += f"*청산가:* `{fmt_coin2(msg['close_rate'], msg['quote_currency'])}`"

        if is_sub_trade:
            stake_amount_fiat = self.__format_profit_fiat(msg, "stake_amount")

            rem = fmt_coin(msg["stake_amount"], msg["quote_currency"])
            message += f"\n*잔여:* `{rem}{stake_amount_fiat}`"
        else:
            duration_str = str(duration)
            message += f"\n*보유 기간:* `{duration_str} ({duration_min:.1f}분)`"
        return message

    def __format_profit_fiat(
        self, msg: RPCExitMsg, key: Literal["stake_amount", "profit_amount", "cumulative_profit"]
    ) -> str:
        """
        Format Fiat currency to append to regular profit output
        """
        profit_fiat_extra = ""
        if self._rpc._fiat_converter and (fiat_currency := msg.get("fiat_currency")):
            profit_fiat = self._rpc._fiat_converter.convert_amount(
                msg[key], msg["stake_currency"], fiat_currency
            )
            profit_fiat_extra = f" / {profit_fiat:.3f} {fiat_currency}"
        return profit_fiat_extra

    def compose_message(self, msg: RPCSendMsg) -> str | None:
        if msg["type"] == RPCMessageType.ENTRY or msg["type"] == RPCMessageType.ENTRY_FILL:
            message = self._format_entry_msg(msg)

        elif msg["type"] == RPCMessageType.EXIT or msg["type"] == RPCMessageType.EXIT_FILL:
            message = self._format_exit_msg(msg)

        elif (
            msg["type"] == RPCMessageType.ENTRY_CANCEL or msg["type"] == RPCMessageType.EXIT_CANCEL
        ):
            message_side = "진입" if msg["type"] == RPCMessageType.ENTRY_CANCEL else "청산"
            message = (
                f"\N{WARNING SIGN} *{self._exchange_from_msg(msg)}:* "
                f"{'부분 ' if msg.get('sub_trade') else ''}"
                f"{message_side} 주문 취소 중: {msg['pair']} "
                f"(#{msg['trade_id']}). 사유: {msg['reason']}."
            )

        elif msg["type"] == RPCMessageType.PROTECTION_TRIGGER:
            message = (
                f"*보호 기능*이 {msg['reason']}로 인해 작동했습니다. "
                f"`{msg['pair']}`는 `{msg['lock_end_time']}`까지 잠금됩니다."
            )

        elif msg["type"] == RPCMessageType.PROTECTION_TRIGGER_GLOBAL:
            message = (
                f"*보호 기능*이 {msg['reason']}로 인해 작동했습니다. "
                f"*모든 페어*가 `{msg['lock_end_time']}`까지 잠금됩니다."
            )

        elif msg["type"] == RPCMessageType.STATUS:
            # "process died" 메시지는 구조화된 재시작 알림으로 변경
            if "process died" in msg['status'].lower():
                message = (
                    "🔄 *봇 재시작 알림*\n"
                    "━━━━━━━━━━━━━━━━\n"
                    "\n"
                    "⚠️ *상태:* `프로세스 종료됨`\n"
                    "\n"
                    "봇이 재시작됩니다.\n"
                    "잠시 후 자동으로 다시 시작됩니다.\n"
                    "\n"
                    "━━━━━━━━━━━━━━━━"
                )
            # 이미 구조화된 메시지인 경우 그대로 표시 (예: 재시작 알림)
            elif "━━━━━━━━━━━━━━━━" in msg['status'] or "봇 재시작 알림" in msg['status']:
                message = msg['status']
            else:
                # 일반 상태 메시지는 한국어로 변환
                from freqtrade.rpc.rpc_manager import format_state_korean
                state_korean = format_state_korean(msg['status'])
                message = f"*상태:* `{state_korean}`"

        elif msg["type"] == RPCMessageType.WARNING:
            message = f"\N{WARNING SIGN} *경고:* `{msg['status']}`"
        elif msg["type"] == RPCMessageType.EXCEPTION:
            # Errors will contain exceptions, which are wrapped in triple ticks.
            message = f"\N{WARNING SIGN} *오류:* \n {msg['status']}"

        elif msg["type"] == RPCMessageType.STARTUP:
            message = f"{msg['status']}"
        elif msg["type"] == RPCMessageType.STRATEGY_MSG:
            message = f"{msg['msg']}"
        else:
            logger.debug("Unknown message type: %s", msg["type"])
            return None
        return message

    def _message_loudness(self, msg: RPCSendMsg) -> str:
        """Determine the loudness of the message - on, off or silent"""
        default_noti = "on"

        msg_type = msg["type"]
        noti = ""
        if msg["type"] == RPCMessageType.EXIT or msg["type"] == RPCMessageType.EXIT_FILL:
            sell_noti = (
                self._config["telegram"].get("notification_settings", {}).get(str(msg_type), {})
            )

            # For backward compatibility sell still can be string
            if isinstance(sell_noti, str):
                noti = sell_noti
            else:
                default_noti = sell_noti.get("*", default_noti)
                noti = sell_noti.get(str(msg["exit_reason"]), default_noti)
        else:
            noti = (
                self._config["telegram"]
                .get("notification_settings", {})
                .get(str(msg_type), default_noti)
            )

        return noti

    def send_msg(self, msg: RPCSendMsg) -> None:
        """Send a message to telegram channel"""
        noti = self._message_loudness(msg)

        if noti == "off":
            logger.info(f"Notification '{msg['type']}' not sent.")
            # Notification disabled
            return

        message = self.compose_message(deepcopy(msg))
        if message:
            asyncio.run_coroutine_threadsafe(
                self._send_msg(message, disable_notification=(noti == "silent")), self._loop
            )

    def _get_exit_emoji(self, msg):
        """
        Get emoji for exit-messages
        """

        if float(msg["profit_ratio"]) >= 0.05:
            return "\N{ROCKET}"
        elif float(msg["profit_ratio"]) >= 0.0:
            return "\N{EIGHT SPOKED ASTERISK}"
        elif msg["exit_reason"] == "stop_loss":
            return "\N{WARNING SIGN}"
        else:
            return "\N{CROSS MARK}"

    def _prepare_order_details(self, filled_orders: list, quote_currency: str, is_open: bool):
        """
        Prepare details of trade with entry adjustment enabled
        """
        lines_detail: list[str] = []
        if len(filled_orders) > 0:
            first_avg = filled_orders[0]["safe_price"]
        order_nr = 0
        for order in filled_orders:
            lines: list[str] = []
            if order["is_open"] is True:
                continue
            order_nr += 1
            wording = "Entry" if order["ft_is_entry"] else "Exit"

            cur_entry_amount = order["filled"] or order["amount"]
            cur_entry_average = order["safe_price"]
            lines.append("  ")
            lines.append(f"*{wording} #{order_nr}:*")
            if order_nr == 1:
                lines.append(
                    f"*Amount:* {round_value(cur_entry_amount, 8)} "
                    f"({fmt_coin(order['cost'], quote_currency)})"
                )
                lines.append(f"*Average Price:* {round_value(cur_entry_average, 8)}")
            else:
                # TODO: This calculation ignores fees.
                price_to_1st_entry = (cur_entry_average - first_avg) / first_avg
                if is_open:
                    lines.append("({})".format(dt_humanize_delta(order["order_filled_date"])))
                lines.append(
                    f"*Amount:* {round_value(cur_entry_amount, 8)} "
                    f"({fmt_coin(order['cost'], quote_currency)})"
                )
                lines.append(
                    f"*Average {wording} Price:* {round_value(cur_entry_average, 8)} "
                    f"({price_to_1st_entry:.2%} from 1st entry rate)"
                )
                lines.append(f"*Order Filled:* {order['order_filled_date']}")

            lines_detail.append("\n".join(lines))

        return lines_detail

    @authorized_only
    async def _order(self, update: Update, context: CallbackContext) -> None:
        """
        Handler for /order.
        Returns the orders of the trade
        :param bot: telegram bot
        :param update: message update
        :return: None
        """

        trade_ids = []
        if context.args and len(context.args) > 0:
            trade_ids = [int(i) for i in context.args if i.isnumeric()]

        results = self._rpc._rpc_trade_status(trade_ids=trade_ids)
        for r in results:
            lines = ["*Order List for Trade #*`{trade_id}`"]

            lines_detail = self._prepare_order_details(
                r["orders"], r["quote_currency"], r["is_open"]
            )
            lines.extend(lines_detail if lines_detail else "")
            await self.__send_order_msg(lines, r)

    async def __send_order_msg(self, lines: list[str], r: dict[str, Any]) -> None:
        """
        Send status message.
        """
        msg = ""

        for line in lines:
            if line:
                if (len(msg) + len(line) + 1) < MAX_MESSAGE_LENGTH:
                    msg += line + "\n"
                else:
                    await self._send_msg(msg.format(**r))
                    msg = "*Order List for Trade #*`{trade_id}` - continued\n" + line + "\n"

        await self._send_msg(msg.format(**r))

    @authorized_only
    async def _status(self, update: Update, context: CallbackContext) -> None:
        """
        Handler for /status.
        Returns the current TradeThread status
        :param bot: telegram bot
        :param update: message update
        :return: None
        """

        if context.args and "table" in context.args:
            await self._status_table(update, context)
            return
        else:
            await self._status_msg(update, context)

    async def _status_msg(self, update: Update, context: CallbackContext) -> None:
        """
        handler for `/status` and `/status <id>`.

        """
        # Check if there's at least one numerical ID provided.
        # If so, try to get only these trades.
        trade_ids = []
        if context.args and len(context.args) > 0:
            trade_ids = [int(i) for i in context.args if i.isnumeric()]

        results = self._rpc._rpc_trade_status(trade_ids=trade_ids)
        position_adjust = self._config.get("position_adjustment_enable", False)
        max_entries = self._config.get("max_entry_position_adjustment", -1)
        for r in results:
            r["open_date_hum"] = dt_humanize_delta(r["open_date"])
            r["num_entries"] = len([o for o in r["orders"] if o["ft_is_entry"]])
            r["num_exits"] = len(
                [
                    o
                    for o in r["orders"]
                    if not o["ft_is_entry"] and not o["ft_order_side"] == "stoploss"
                ]
            )
            r["exit_reason"] = r.get("exit_reason", "")
            r["stake_amount_r"] = fmt_coin(r["stake_amount"], r["quote_currency"])
            r["max_stake_amount_r"] = fmt_coin(
                r["max_stake_amount"] or r["stake_amount"], r["quote_currency"]
            )
            r["profit_abs_r"] = fmt_coin(r["profit_abs"], r["quote_currency"])
            r["realized_profit_r"] = fmt_coin(r["realized_profit"], r["quote_currency"])
            r["total_profit_abs_r"] = fmt_coin(r["total_profit_abs"], r["quote_currency"])
            lines = [
                "*Trade ID:* `{trade_id}`" + (" `(since {open_date_hum})`" if r["is_open"] else ""),
                "*Current Pair:* {pair}",
                (
                    f"*Direction:* {'`Short`' if r.get('is_short') else '`Long`'}"
                    + " ` ({leverage}x)`"
                    if r.get("leverage")
                    else ""
                ),
                "*Amount:* `{amount} ({stake_amount_r})`",
                "*Total invested:* `{max_stake_amount_r}`" if position_adjust else "",
                "*Enter Tag:* `{enter_tag}`" if r["enter_tag"] else "",
                "*Exit Reason:* `{exit_reason}`" if r["exit_reason"] else "",
            ]

            if position_adjust:
                max_buy_str = f"/{max_entries + 1}" if (max_entries > 0) else ""
                lines.extend(
                    [
                        "*Number of Entries:* `{num_entries}" + max_buy_str + "`",
                        "*Number of Exits:* `{num_exits}`",
                    ]
                )

            lines.extend(
                [
                    f"*Open Rate:* `{round_value(r['open_rate'], 8)}`",
                    f"*Close Rate:* `{round_value(r['close_rate'], 8)}`" if r["close_rate"] else "",
                    "*Open Date:* `{open_date}`",
                    "*Close Date:* `{close_date}`" if r["close_date"] else "",
                    (
                        f" \n*Current Rate:* `{round_value(r['current_rate'], 8)}`"
                        if r["is_open"]
                        else ""
                    ),
                    ("*Unrealized Profit:* " if r["is_open"] else "*Close Profit: *")
                    + "`{profit_ratio:.2%}` `({profit_abs_r})`",
                ]
            )

            if r["is_open"]:
                if r.get("realized_profit"):
                    lines.extend(
                        [
                            "*Realized Profit:* `{realized_profit_ratio:.2%} "
                            "({realized_profit_r})`",
                            "*Total Profit:* `{total_profit_ratio:.2%} ({total_profit_abs_r})`",
                        ]
                    )

                # Append empty line to improve readability
                lines.append(" ")
                if (
                    r["stop_loss_abs"] != r["initial_stop_loss_abs"]
                    and r["initial_stop_loss_ratio"] is not None
                ):
                    # Adding initial stoploss only if it is different from stoploss
                    lines.append(
                        "*Initial Stoploss:* `{initial_stop_loss_abs:.8f}` "
                        "`({initial_stop_loss_ratio:.2%})`"
                    )

                # Adding stoploss and stoploss percentage only if it is not None
                lines.append(
                    f"*Stoploss:* `{round_value(r['stop_loss_abs'], 8)}` "
                    + ("`({stop_loss_ratio:.2%})`" if r["stop_loss_ratio"] else "")
                )
                lines.append(
                    f"*Stoploss distance:* `{round_value(r['stoploss_current_dist'], 8)}` "
                    "`({stoploss_current_dist_ratio:.2%})`"
                )
                if r.get("open_orders"):
                    lines.append(
                        "*Open Order:* `{open_orders}`"
                        + ("- `{exit_order_status}`" if r["exit_order_status"] else "")
                    )

            await self.__send_status_msg(lines, r)

    async def __send_status_msg(self, lines: list[str], r: dict[str, Any]) -> None:
        """
        Send status message.
        """
        msg = ""

        for line in lines:
            if line:
                if (len(msg) + len(line) + 1) < MAX_MESSAGE_LENGTH:
                    msg += line + "\n"
                else:
                    await self._send_msg(msg.format(**r))
                    msg = "*Trade ID:* `{trade_id}` - continued\n" + line + "\n"

        await self._send_msg(msg.format(**r))

    @authorized_only
    async def _status_table(self, update: Update, context: CallbackContext) -> None:
        """
        Handler for /status table.
        Returns the current TradeThread status in table format
        :param bot: telegram bot
        :param update: message update
        :return: None
        """
        fiat_currency = self._config.get("fiat_display_currency", "")
        statlist, head, fiat_profit_sum, fiat_total_profit_sum = self._rpc._rpc_status_table(
            self._config["stake_currency"], fiat_currency
        )

        show_total = not isnan(fiat_profit_sum) and len(statlist) > 1
        show_total_realized = (
            not isnan(fiat_total_profit_sum) and len(statlist) > 1 and fiat_profit_sum
        ) != fiat_total_profit_sum
        max_trades_per_msg = 50
        """
        Calculate the number of messages of 50 trades per message
        0.99 is used to make sure that there are no extra (empty) messages
        As an example with 50 trades, there will be int(50/50 + 0.99) = 1 message
        """
        messages_count = max(int(len(statlist) / max_trades_per_msg + 0.99), 1)
        for i in range(0, messages_count):
            trades = statlist[i * max_trades_per_msg : (i + 1) * max_trades_per_msg]
            if show_total and i == messages_count - 1:
                # append total line
                trades.append(["Total", "", "", f"{fiat_profit_sum:.2f} {fiat_currency}"])
                if show_total_realized:
                    trades.append(
                        [
                            "Total",
                            "(incl. realized Profits)",
                            "",
                            f"{fiat_total_profit_sum:.2f} {fiat_currency}",
                        ]
                    )

            message = tabulate(trades, headers=head, tablefmt="simple")
            if show_total and i == messages_count - 1:
                # insert separators line between Total
                lines = message.split("\n")
                offset = 2 if show_total_realized else 1
                message = "\n".join(lines[:-offset] + [lines[1]] + lines[-offset:])
            await self._send_msg(
                f"<pre>{message}</pre>",
                parse_mode=ParseMode.HTML,
                reload_able=True,
                callback_path="update_status_table",
                query=update.callback_query,
            )

    async def _timeunit_stats(self, update: Update, context: CallbackContext, unit: str) -> None:
        """
        Handler for /daily <n>
        Returns a daily profit (in BTC) over the last n days.
        :param bot: telegram bot
        :param update: message update
        :return: None
        """

        vals = {
            "days": TimeunitMappings("Day", "Daily", "days", "update_daily", 7, "%Y-%m-%d"),
            "weeks": TimeunitMappings(
                "Monday", "Weekly", "weeks (starting from Monday)", "update_weekly", 8, "%Y-%m-%d"
            ),
            "months": TimeunitMappings("Month", "Monthly", "months", "update_monthly", 6, "%Y-%m"),
        }
        val = vals[unit]

        stake_cur = self._config["stake_currency"]
        fiat_disp_cur = self._config.get("fiat_display_currency", "")
        try:
            timescale = int(context.args[0]) if context.args else val.default
        except (TypeError, ValueError, IndexError):
            timescale = val.default
        stats = self._rpc._rpc_timeunit_profit(timescale, stake_cur, fiat_disp_cur, unit)
        stats_tab = tabulate(
            [
                [
                    f"{period['date']:{val.dateformat}} ({period['trade_count']})",
                    f"{fmt_coin(period['abs_profit'], stats['stake_currency'])}",
                    f"{period['fiat_value']:.2f} {stats['fiat_display_currency']}",
                    f"{period['rel_profit']:.2%}",
                ]
                for period in stats["data"]
            ],
            headers=[
                f"{val.header} (count)",
                f"{stake_cur}",
                f"{fiat_disp_cur}",
                "Profit %",
                "Trades",
            ],
            tablefmt="simple",
        )
        message = (
            f"<b>{val.message} Profit over the last {timescale} {val.message2}</b>:\n"
            f"<pre>{stats_tab}</pre>"
        )
        await self._send_msg(
            message,
            parse_mode=ParseMode.HTML,
            reload_able=True,
            callback_path=val.callback,
            query=update.callback_query,
        )

    @authorized_only
    async def _daily(self, update: Update, context: CallbackContext) -> None:
        """
        Handler for /daily <n>
        Returns a daily profit (in BTC) over the last n days.
        :param bot: telegram bot
        :param update: message update
        :return: None
        """
        await self._timeunit_stats(update, context, "days")

    @authorized_only
    async def _weekly(self, update: Update, context: CallbackContext) -> None:
        """
        Handler for /weekly <n>
        Returns a weekly profit (in BTC) over the last n weeks.
        :param bot: telegram bot
        :param update: message update
        :return: None
        """
        await self._timeunit_stats(update, context, "weeks")

    @authorized_only
    async def _monthly(self, update: Update, context: CallbackContext) -> None:
        """
        Handler for /monthly <n>
        Returns a monthly profit (in BTC) over the last n months.
        :param bot: telegram bot
        :param update: message update
        :return: None
        """
        await self._timeunit_stats(update, context, "months")

    def _format_profit_message(
        self,
        stats: dict,
        stake_cur: str,
        fiat_disp_cur: str,
        timescale: int | None = None,
        direction: str | None = None,
    ) -> str:
        """
        Format profit statistics message for telegram.

        :param stats: Trade statistics dictionary
        :param stake_cur: Stake currency
        :param fiat_disp_cur: Fiat display currency
        :param timescale: Optional timescale filter
        :param direction: Optional direction filter ('long', 'short', or None for all)
        :return: Formatted markdown message
        """
        # Extract common variables
        profit_closed_coin = stats["profit_closed_coin"]
        profit_closed_ratio_mean = stats["profit_closed_ratio_mean"]
        profit_closed_percent = stats["profit_closed_percent"]
        profit_closed_fiat = stats["profit_closed_fiat"]
        profit_all_coin = stats["profit_all_coin"]
        profit_all_ratio_mean = stats["profit_all_ratio_mean"]
        profit_all_percent = stats["profit_all_percent"]
        profit_all_fiat = stats["profit_all_fiat"]
        trade_count = stats["trade_count"]
        first_trade_date = f"{stats['first_trade_humanized']} ({stats['first_trade_date']})"
        latest_trade_date = f"{stats['latest_trade_humanized']} ({stats['latest_trade_date']})"
        avg_duration = stats["avg_duration"]
        best_pair = stats["best_pair"]
        best_pair_profit_ratio = stats["best_pair_profit_ratio"]
        best_pair_profit_abs = fmt_coin(stats["best_pair_profit_abs"], stake_cur)
        winrate = stats["winrate"]
        expectancy = stats["expectancy"]
        expectancy_ratio = stats["expectancy_ratio"]

        # Direction-specific labels
        direction_label = f" {direction}" if direction else ""
        no_trades_msg = (
            f"No{direction_label} trades yet.\n*Bot started:* `{stats['bot_start_date']}`"
        )
        no_closed_msg = f"`No closed{direction_label} trade` \n"
        closed_roi_label = f"*ROI:* Closed{direction_label} trades"
        all_roi_label = f"*ROI:* All{direction_label} trades"

        if stats["trade_count"] == 0:
            return no_trades_msg

        # Build message
        if stats["closed_trade_count"] > 0:
            fiat_closed_trades = (
                f"∙ `{fmt_coin(profit_closed_fiat, fiat_disp_cur)}`\n" if fiat_disp_cur else ""
            )
            markdown_msg = (
                f"{closed_roi_label}\n"
                f"∙ `{fmt_coin(profit_closed_coin, stake_cur)} "
                f"({profit_closed_ratio_mean:.2%}) "
                f"({profit_closed_percent} \N{GREEK CAPITAL LETTER SIGMA}%)`\n"
                f"{fiat_closed_trades}"
            )
        else:
            markdown_msg = no_closed_msg

        fiat_all_trades = (
            f"∙ `{fmt_coin(profit_all_fiat, fiat_disp_cur)}`\n" if fiat_disp_cur else ""
        )
        markdown_msg += (
            f"{all_roi_label}\n"
            f"∙ `{fmt_coin(profit_all_coin, stake_cur)} "
            f"({profit_all_ratio_mean:.2%}) "
            f"({profit_all_percent} \N{GREEK CAPITAL LETTER SIGMA}%)`\n"
            f"{fiat_all_trades}"
            f"*Total Trade Count:* `{trade_count}`\n"
            f"*Bot started:* `{stats['bot_start_date']}`\n"
            f"*{'First Trade opened' if not timescale else 'Showing Profit since'}:* "
            f"`{first_trade_date}`\n"
            f"*Latest Trade opened:* `{latest_trade_date}`\n"
            f"*Win / Loss:* `{stats['winning_trades']} / {stats['losing_trades']}`\n"
            f"*Winrate:* `{winrate:.2%}`\n"
            f"*Expectancy (Ratio):* `{expectancy:.2f} ({expectancy_ratio:.2f})`"
        )

        if stats["closed_trade_count"] > 0:
            markdown_msg += (
                f"\n*Avg. Duration:* `{avg_duration}`\n"
                f"*Best Performing:* `{best_pair}: {best_pair_profit_abs} "
                f"({best_pair_profit_ratio:.2%})`\n"
                f"*Trading volume:* `{fmt_coin(stats['trading_volume'], stake_cur)}`\n"
                f"*Profit factor:* `{stats['profit_factor']:.2f}`\n"
                f"*Max Drawdown:* `{stats['max_drawdown']:.2%} "
                f"({fmt_coin(stats['max_drawdown_abs'], stake_cur)})`\n"
                f"    from `{stats['max_drawdown_start']} "
                f"({fmt_coin(stats['drawdown_high'], stake_cur)})`\n"
                f"    to `{stats['max_drawdown_end']} "
                f"({fmt_coin(stats['drawdown_low'], stake_cur)})`\n"
                f"*Current Drawdown:* `{stats['current_drawdown']:.2%} "
                f"({fmt_coin(stats['current_drawdown_abs'], stake_cur)})`\n"
                f"    from `{stats['current_drawdown_start']} "
                f"({fmt_coin(stats['current_drawdown_high'], stake_cur)})`\n"
            )

        return markdown_msg

    async def _profit_handler(
        self,
        update: Update,
        context: CallbackContext,
        direction: str | None = None,
    ) -> None:
        """
        Common handler for profit commands.

        :param update: Telegram update
        :param context: Callback context
        :param direction: Trade direction filter ('long', 'short', or None)
        :param callback_path: Callback path for message updates
        """
        stake_cur = self._config["stake_currency"]
        fiat_disp_cur = self._config.get("fiat_display_currency", "")

        start_date = datetime.fromtimestamp(0)
        timescale = None
        try:
            if context.args:
                if not direction:
                    arg = context.args[0].lower()
                    if arg in ("short", "long"):
                        direction = arg
                        context.args.pop(0)  # Remove direction from args
                timescale = int(context.args[0]) - 1
                today_start = datetime.combine(date.today(), datetime.min.time())
                start_date = today_start - timedelta(days=timescale)
        except (TypeError, ValueError, IndexError):
            pass

        # Get stats with optional direction filter
        stats_kwargs = {
            "stake_currency": stake_cur,
            "fiat_display_currency": fiat_disp_cur,
            "start_date": start_date,
        }
        if direction:
            stats_kwargs["direction"] = direction

        stats = self._rpc._rpc_trade_statistics(**stats_kwargs)
        markdown_msg = self._format_profit_message(
            stats, stake_cur, fiat_disp_cur, timescale, direction
        )

        await self._send_msg(
            markdown_msg,
            reload_able=True,
            callback_path="update_profit" if not direction else f"update_profit_{direction}",
            query=update.callback_query,
        )

    @authorized_only
    async def _profit(self, update: Update, context: CallbackContext) -> None:
        """
        Handler for /profit.
        Returns a cumulative profit statistics.
        :param bot: telegram bot
        :param update: message update
        :return: None
        """
        await self._profit_handler(update, context)

    @authorized_only
    async def _profit_long(self, update: Update, context: CallbackContext) -> None:
        """
        Handler for /profit_long.
        Returns cumulative profit statistics for long trades.
        """
        await self._profit_handler(update, context, direction="long")

    @authorized_only
    async def _profit_short(self, update: Update, context: CallbackContext) -> None:
        """
        Handler for /profit_short.
        Returns cumulative profit statistics for short trades.
        """
        await self._profit_handler(update, context, direction="short")

    @authorized_only
    async def _stats(self, update: Update, context: CallbackContext) -> None:
        """
        Handler for /stats
        Show stats of recent trades
        """
        stats = self._rpc._rpc_stats()

        reason_map = {
            "roi": "ROI",
            "stop_loss": "Stoploss",
            "trailing_stop_loss": "Trail. Stop",
            "stoploss_on_exchange": "Stoploss",
            "exit_signal": "Exit Signal",
            "force_exit": "Force Exit",
            "emergency_exit": "Emergency Exit",
        }
        exit_reasons_tabulate = [
            [reason_map.get(reason, reason), sum(count.values()), count["wins"], count["losses"]]
            for reason, count in stats["exit_reasons"].items()
        ]
        exit_reasons_msg = "No trades yet."
        for reason in chunks(exit_reasons_tabulate, 25):
            exit_reasons_msg = tabulate(reason, headers=["Exit Reason", "Exits", "Wins", "Losses"])
            if len(exit_reasons_tabulate) > 25:
                await self._send_msg(f"```\n{exit_reasons_msg}```", ParseMode.MARKDOWN)
                exit_reasons_msg = ""

        durations = stats["durations"]
        duration_msg = tabulate(
            [
                [
                    "Wins",
                    (
                        str(timedelta(seconds=durations["wins"]))
                        if durations["wins"] is not None
                        else "N/A"
                    ),
                ],
                [
                    "Losses",
                    (
                        str(timedelta(seconds=durations["losses"]))
                        if durations["losses"] is not None
                        else "N/A"
                    ),
                ],
            ],
            headers=["", "Avg. Duration"],
        )
        msg = f"""```\n{exit_reasons_msg}```\n```\n{duration_msg}```"""

        await self._send_msg(msg, ParseMode.MARKDOWN)

    @authorized_only
    async def _balance(self, update: Update, context: CallbackContext) -> None:
        """Handler for /balance"""
        full_result = context.args and "full" in context.args
        result = self._rpc._rpc_balance(
            self._config["stake_currency"], self._config.get("fiat_display_currency", "")
        )

        balance_dust_level = self._config["telegram"].get("balance_dust_level", 0.0)
        if not balance_dust_level:
            balance_dust_level = DUST_PER_COIN.get(self._config["stake_currency"], 1.0)

        output = ""
        if self._config["dry_run"]:
            output += "*Warning:* Simulated balances in Dry Mode.\n"
        starting_cap = fmt_coin(result["starting_capital"], self._config["stake_currency"])
        output += f"Starting capital: `{starting_cap}`"
        starting_cap_fiat = (
            fmt_coin(result["starting_capital_fiat"], self._config["fiat_display_currency"])
            if result["starting_capital_fiat"] > 0
            else ""
        )
        output += (f" `, {starting_cap_fiat}`.\n") if result["starting_capital_fiat"] > 0 else ".\n"

        total_dust_balance = 0
        total_dust_currencies = 0
        for curr in result["currencies"]:
            curr_output = ""
            if (curr["is_position"] or curr["est_stake"] > balance_dust_level) and (
                full_result or curr["is_bot_managed"]
            ):
                if curr["is_position"]:
                    curr_output = (
                        f"*{curr['currency']}:*\n"
                        f"\t`{curr['side']}: {curr['position']:.8f}`\n"
                        f"\t`Est. {curr['stake']}: "
                        f"{fmt_coin(curr['est_stake'], curr['stake'], False)}`\n"
                    )
                else:
                    est_stake = fmt_coin(
                        curr["est_stake" if full_result else "est_stake_bot"], curr["stake"], False
                    )

                    curr_output = (
                        f"*{curr['currency']}:*\n"
                        f"\t`Available: {curr['free']:.8f}`\n"
                        f"\t`Balance: {curr['balance']:.8f}`\n"
                        f"\t`Pending: {curr['used']:.8f}`\n"
                        f"\t`Bot Owned: {curr['bot_owned']:.8f}`\n"
                        f"\t`Est. {curr['stake']}: {est_stake}`\n"
                    )

            elif curr["est_stake"] <= balance_dust_level:
                total_dust_balance += curr["est_stake"]
                total_dust_currencies += 1

            # Handle overflowing message length
            if len(output + curr_output) >= MAX_MESSAGE_LENGTH:
                await self._send_msg(output)
                output = curr_output
            else:
                output += curr_output

        if total_dust_balance > 0:
            output += (
                f"*{total_dust_currencies} Other "
                f"{plural(total_dust_currencies, 'Currency', 'Currencies')} "
                f"(< {balance_dust_level} {result['stake']}):*\n"
                f"\t`Est. {result['stake']}: "
                f"{fmt_coin(total_dust_balance, result['stake'], False)}`\n"
            )
        tc = result["trade_count"] > 0
        stake_improve = f" `({result['starting_capital_ratio']:.2%})`" if tc else ""
        fiat_val = f" `({result['starting_capital_fiat_ratio']:.2%})`" if tc else ""
        value = fmt_coin(result["value" if full_result else "value_bot"], result["symbol"], False)
        total_stake = fmt_coin(
            result["total" if full_result else "total_bot"], result["stake"], False
        )
        fiat_estimated_value = (
            f"\t`{result['symbol']}: {value}`{fiat_val}\n" if result["symbol"] else ""
        )
        output += (
            f"\n*Estimated Value{' (Bot managed assets only)' if not full_result else ''}*:\n"
            f"\t`{result['stake']}: {total_stake}`{stake_improve}\n"
            f"{fiat_estimated_value}"
        )
        await self._send_msg(
            output, reload_able=True, callback_path="update_balance", query=update.callback_query
        )

    @authorized_only
    async def _start(self, update: Update, context: CallbackContext) -> None:
        """
        Handler for /start.
        Starts TradeThread
        :param bot: telegram bot
        :param update: message update
        :return: None
        """
        msg = self._rpc._rpc_start()
        await self._send_msg(f"상태: `{msg['status']}`")

    @authorized_only
    async def _stop(self, update: Update, context: CallbackContext) -> None:
        """
        Handler for /stop.
        Stops TradeThread
        :param bot: telegram bot
        :param update: message update
        :return: None
        """
        msg = self._rpc._rpc_stop()
        await self._send_msg(f"상태: `{msg['status']}`")

    @authorized_only
    async def _reload_config(self, update: Update, context: CallbackContext) -> None:
        """
        Handler for /reload_config.
        Triggers a config file reload
        :param bot: telegram bot
        :param update: message update
        :return: None
        """
        msg = self._rpc._rpc_reload_config()
        await self._send_msg(f"상태: `{msg['status']}`")

    @authorized_only
    async def _pause(self, update: Update, context: CallbackContext) -> None:
        """
        Handler for /stop_buy /stop_entry and /pause.
        Sets bot state to paused
        :param bot: telegram bot
        :param update: message update
        :return: None
        """
        msg = self._rpc._rpc_pause()
        await self._send_msg(f"상태: `{msg['status']}`")

    @authorized_only
    async def _reload_trade_from_exchange(self, update: Update, context: CallbackContext) -> None:
        """
        Handler for /reload_trade <tradeid>.
        """
        if not context.args or len(context.args) == 0:
            raise RPCException("Trade-id not set.")
        trade_id = int(context.args[0])
        msg = self._rpc._rpc_reload_trade_from_exchange(trade_id)
        await self._send_msg(f"상태: `{msg['status']}`")

    @authorized_only
    async def _force_exit(self, update: Update, context: CallbackContext) -> None:
        """
        Handler for /forceexit <id>.
        Sells the given trade at current price
        :param bot: telegram bot
        :param update: message update
        :return: None
        """

        if context.args:
            trade_id = context.args[0]
            await self._force_exit_action(trade_id)
        else:
            fiat_currency = self._config.get("fiat_display_currency", "")
            try:
                statlist, _, _, _ = self._rpc._rpc_status_table(
                    self._config["stake_currency"], fiat_currency
                )
            except RPCException:
                await self._send_msg(msg="No open trade found.")
                return
            trades = []
            for trade in statlist:
                trades.append((trade[0], f"{trade[0]} {trade[1]} {trade[2]} {trade[3]}"))

            trade_buttons = [
                InlineKeyboardButton(text=trade[1], callback_data=f"force_exit__{trade[0]}")
                for trade in trades
            ]
            buttons_aligned = self._layout_inline_keyboard(trade_buttons, cols=1)

            buttons_aligned.append(
                [InlineKeyboardButton(text="Cancel", callback_data="force_exit__cancel")]
            )
            await self._send_msg(msg="Which trade?", keyboard=buttons_aligned)

    async def _force_exit_action(self, trade_id: str):
        if trade_id != "cancel":
            try:
                loop = asyncio.get_running_loop()
                # Workaround to avoid nested loops
                await loop.run_in_executor(None, safe_async_db(self._rpc._rpc_force_exit), trade_id)
            except RPCException as e:
                await self._send_msg(str(e))

    async def _force_exit_inline(self, update: Update, _: CallbackContext) -> None:
        if update.callback_query:
            query = update.callback_query
            if query.data and "__" in query.data:
                # Input data is "force_exit__<tradid|cancel>"
                trade_id = query.data.split("__")[1].split(" ")[0]
                if trade_id == "cancel":
                    await query.answer()
                    await query.edit_message_text(text="Force exit canceled.")
                    return
                trade: Trade | None = Trade.get_trades(trade_filter=Trade.id == trade_id).first()
                await query.answer()
                if trade:
                    await query.edit_message_text(
                        text=f"Manually exiting Trade #{trade_id}, {trade.pair}"
                    )
                    await self._force_exit_action(trade_id)
                else:
                    await query.edit_message_text(text=f"Trade {trade_id} not found.")

    async def _force_enter_action(self, pair, price: float | None, order_side: SignalDirection):
        if pair != "cancel":
            try:

                @safe_async_db
                def _force_enter():
                    self._rpc._rpc_force_entry(pair, price, order_side=order_side)

                loop = asyncio.get_running_loop()
                # Workaround to avoid nested loops
                await loop.run_in_executor(None, _force_enter)
            except RPCException as e:
                logger.exception("Forcebuy error!")
                await self._send_msg(str(e), ParseMode.HTML)

    async def _force_enter_inline(self, update: Update, _: CallbackContext) -> None:
        if update.callback_query:
            query = update.callback_query
            if query.data and "__" in query.data:
                # Input data is "force_enter__<pair|cancel>_<side>"
                payload = query.data.split("__")[1]
                if payload == "cancel":
                    await query.answer()
                    await query.edit_message_text(text="Force enter canceled.")
                    return
                if payload and "_||_" in payload:
                    pair, side = payload.split("_||_")
                    order_side = SignalDirection(side)
                    await query.answer()
                    await query.edit_message_text(text=f"Manually entering {order_side} for {pair}")
                    await self._force_enter_action(pair, None, order_side)

    @staticmethod
    def _layout_inline_keyboard(
        buttons: list[InlineKeyboardButton], cols=3
    ) -> list[list[InlineKeyboardButton]]:
        return [buttons[i : i + cols] for i in range(0, len(buttons), cols)]

    @authorized_only
    async def _force_enter(
        self, update: Update, context: CallbackContext, order_side: SignalDirection
    ) -> None:
        """
        Handler for /forcelong <asset> <price> and `/forceshort <asset> <price>
        Buys a pair trade at the given or current price
        :param bot: telegram bot
        :param update: message update
        :return: None
        """
        if context.args:
            pair = context.args[0]
            price = float(context.args[1]) if len(context.args) > 1 else None
            await self._force_enter_action(pair, price, order_side)
        else:
            whitelist = self._rpc._rpc_whitelist()["whitelist"]
            pair_buttons = [
                InlineKeyboardButton(
                    text=pair, callback_data=f"force_enter__{pair}_||_{order_side}"
                )
                for pair in sorted(whitelist)
            ]
            buttons_aligned = self._layout_inline_keyboard(pair_buttons)

            buttons_aligned.append(
                [InlineKeyboardButton(text="Cancel", callback_data="force_enter__cancel")]
            )
            await self._send_msg(
                msg="Which pair?", keyboard=buttons_aligned, query=update.callback_query
            )

    @authorized_only
    async def _trades(self, update: Update, context: CallbackContext) -> None:
        """
        Handler for /trades <n>
        Returns last n recent trades.
        :param bot: telegram bot
        :param update: message update
        :return: None
        """
        stake_cur = self._config["stake_currency"]
        try:
            nrecent = int(context.args[0]) if context.args else 10
        except (TypeError, ValueError, IndexError):
            nrecent = 10
        nonspot = self._config.get("trading_mode", TradingMode.SPOT) != TradingMode.SPOT
        trades = self._rpc._rpc_trade_history(nrecent)
        trades_tab = tabulate(
            [
                [
                    dt_humanize_delta(dt_from_ts(trade["close_timestamp"])),
                    f"{trade['pair']} (#{trade['trade_id']}"
                    f"{(' ' + ('S' if trade['is_short'] else 'L')) if nonspot else ''})",
                    f"{(trade['close_profit']):.2%} ({trade['close_profit_abs']})",
                ]
                for trade in trades["trades"]
            ],
            headers=[
                "Close Date",
                "Pair (ID L/S)" if nonspot else "Pair (ID)",
                f"Profit ({stake_cur})",
            ],
            tablefmt="simple",
        )
        message = f"<b>{min(trades['trades_count'], nrecent)} recent trades</b>:\n" + (
            f"<pre>{trades_tab}</pre>" if trades["trades_count"] > 0 else ""
        )
        await self._send_msg(message, parse_mode=ParseMode.HTML)

    @authorized_only
    async def _delete_trade(self, update: Update, context: CallbackContext) -> None:
        """
        Handler for /delete <id>.
        Delete the given trade
        :param bot: telegram bot
        :param update: message update
        :return: None
        """
        if not context.args or len(context.args) == 0:
            raise RPCException("Trade-id not set.")
        trade_id = int(context.args[0])
        msg = self._rpc._rpc_delete(trade_id)
        await self._send_msg(
            f"{msg['result_msg']}\n"
            "Please make sure to take care of this asset on the exchange manually."
        )

    @authorized_only
    async def _cancel_open_order(self, update: Update, context: CallbackContext) -> None:
        """
        Handler for /cancel_open_order <id>.
        Cancel open order for tradeid
        :param bot: telegram bot
        :param update: message update
        :return: None
        """
        if not context.args or len(context.args) == 0:
            raise RPCException("Trade-id not set.")
        trade_id = int(context.args[0])
        self._rpc._rpc_cancel_open_order(trade_id)
        await self._send_msg("Open order canceled.")

    @authorized_only
    async def _performance(self, update: Update, context: CallbackContext) -> None:
        """
        Handler for /performance.
        Shows a performance statistic from finished trades
        :param bot: telegram bot
        :param update: message update
        :return: None
        """
        trades = self._rpc._rpc_performance()
        output = "<b>Performance:</b>\n"
        for i, trade in enumerate(trades):
            stat_line = (
                f"{i + 1}.\t <code>{trade['pair']}\t"
                f"{fmt_coin(trade['profit_abs'], self._config['stake_currency'])} "
                f"({trade['profit_ratio']:.2%}) "
                f"({trade['count']})</code>\n"
            )

            if len(output + stat_line) >= MAX_MESSAGE_LENGTH:
                await self._send_msg(output, parse_mode=ParseMode.HTML)
                output = stat_line
            else:
                output += stat_line

        await self._send_msg(
            output,
            parse_mode=ParseMode.HTML,
            reload_able=True,
            callback_path="update_performance",
            query=update.callback_query,
        )

    @authorized_only
    async def _enter_tag_performance(self, update: Update, context: CallbackContext) -> None:
        """
        Handler for /entries PAIR .
        Shows a performance statistic from finished trades
        :param bot: telegram bot
        :param update: message update
        :return: None
        """
        pair = None
        if context.args and isinstance(context.args[0], str):
            pair = context.args[0]

        trades = self._rpc._rpc_enter_tag_performance(pair)
        output = "*Entry Tag Performance:*\n"
        for i, trade in enumerate(trades):
            stat_line = (
                f"{i + 1}.\t `{trade['enter_tag']}\t"
                f"{fmt_coin(trade['profit_abs'], self._config['stake_currency'])} "
                f"({trade['profit_ratio']:.2%}) "
                f"({trade['count']})`\n"
            )

            if len(output + stat_line) >= MAX_MESSAGE_LENGTH:
                await self._send_msg(output, parse_mode=ParseMode.MARKDOWN)
                output = stat_line
            else:
                output += stat_line

        await self._send_msg(
            output,
            parse_mode=ParseMode.MARKDOWN,
            reload_able=True,
            callback_path="update_enter_tag_performance",
            query=update.callback_query,
        )

    @authorized_only
    async def _exit_reason_performance(self, update: Update, context: CallbackContext) -> None:
        """
        Handler for /exits.
        Shows a performance statistic from finished trades
        :param bot: telegram bot
        :param update: message update
        :return: None
        """
        pair = None
        if context.args and isinstance(context.args[0], str):
            pair = context.args[0]

        trades = self._rpc._rpc_exit_reason_performance(pair)
        output = "*Exit Reason Performance:*\n"
        for i, trade in enumerate(trades):
            stat_line = (
                f"{i + 1}.\t `{trade['exit_reason']}\t"
                f"{fmt_coin(trade['profit_abs'], self._config['stake_currency'])} "
                f"({trade['profit_ratio']:.2%}) "
                f"({trade['count']})`\n"
            )

            if len(output + stat_line) >= MAX_MESSAGE_LENGTH:
                await self._send_msg(output, parse_mode=ParseMode.MARKDOWN)
                output = stat_line
            else:
                output += stat_line

        await self._send_msg(
            output,
            parse_mode=ParseMode.MARKDOWN,
            reload_able=True,
            callback_path="update_exit_reason_performance",
            query=update.callback_query,
        )

    @authorized_only
    async def _mix_tag_performance(self, update: Update, context: CallbackContext) -> None:
        """
        Handler for /mix_tags.
        Shows a performance statistic from finished trades
        :param bot: telegram bot
        :param update: message update
        :return: None
        """
        pair = None
        if context.args and isinstance(context.args[0], str):
            pair = context.args[0]

        trades = self._rpc._rpc_mix_tag_performance(pair)
        output = "*Mix Tag Performance:*\n"
        for i, trade in enumerate(trades):
            stat_line = (
                f"{i + 1}.\t `{trade['mix_tag']}\t"
                f"{fmt_coin(trade['profit_abs'], self._config['stake_currency'])} "
                f"({trade['profit_ratio']:.2%}) "
                f"({trade['count']})`\n"
            )

            if len(output + stat_line) >= MAX_MESSAGE_LENGTH:
                await self._send_msg(output, parse_mode=ParseMode.MARKDOWN)
                output = stat_line
            else:
                output += stat_line

        await self._send_msg(
            output,
            parse_mode=ParseMode.MARKDOWN,
            reload_able=True,
            callback_path="update_mix_tag_performance",
            query=update.callback_query,
        )

    @authorized_only
    async def _count(self, update: Update, context: CallbackContext) -> None:
        """
        Handler for /count.
        Returns the number of trades running
        :param bot: telegram bot
        :param update: message update
        :return: None
        """
        counts = self._rpc._rpc_count()
        message = tabulate(
            {k: [v] for k, v in counts.items()},
            headers=["current", "max", "total stake"],
            tablefmt="simple",
        )
        message = f"<pre>{message}</pre>"
        logger.debug(message)
        await self._send_msg(
            message,
            parse_mode=ParseMode.HTML,
            reload_able=True,
            callback_path="update_count",
            query=update.callback_query,
        )

    @authorized_only
    async def _locks(self, update: Update, context: CallbackContext) -> None:
        """
        Handler for /locks.
        Returns the currently active locks
        """
        rpc_locks = self._rpc._rpc_locks()
        if not rpc_locks["locks"]:
            await self._send_msg("No active locks.", parse_mode=ParseMode.HTML)

        for locks in chunks(rpc_locks["locks"], 25):
            message = tabulate(
                [
                    [lock["id"], lock["pair"], lock["lock_end_time"], lock["reason"]]
                    for lock in locks
                ],
                headers=["ID", "Pair", "Until", "Reason"],
                tablefmt="simple",
            )
            message = f"<pre>{escape(message)}</pre>"
            logger.debug(message)
            await self._send_msg(message, parse_mode=ParseMode.HTML)

    @authorized_only
    async def _delete_locks(self, update: Update, context: CallbackContext) -> None:
        """
        Handler for /delete_locks.
        Returns the currently active locks
        """
        arg = context.args[0] if context.args and len(context.args) > 0 else None
        lockid = None
        pair = None
        if arg:
            try:
                lockid = int(arg)
            except ValueError:
                pair = arg

        self._rpc._rpc_delete_lock(lockid=lockid, pair=pair)
        await self._locks(update, context)

    @authorized_only
    async def _whitelist(self, update: Update, context: CallbackContext) -> None:
        """
        Handler for /whitelist
        Shows the currently active whitelist
        """
        whitelist = self._rpc._rpc_whitelist()

        if context.args:
            if "sorted" in context.args:
                whitelist["whitelist"] = sorted(whitelist["whitelist"])
            if "baseonly" in context.args:
                whitelist["whitelist"] = [pair.split("/")[0] for pair in whitelist["whitelist"]]

        message = f"Using whitelist `{whitelist['method']}` with {whitelist['length']} pairs\n"
        message += f"`{', '.join(whitelist['whitelist'])}`"

        logger.debug(message)
        await self._send_msg(message)

    @authorized_only
    async def _blacklist(self, update: Update, context: CallbackContext) -> None:
        """
        Handler for /blacklist
        Shows the currently active blacklist
        """
        await self.send_blacklist_msg(self._rpc._rpc_blacklist(context.args))

    async def send_blacklist_msg(self, blacklist: dict):
        errmsgs = []
        for _, error in blacklist["errors"].items():
            errmsgs.append(f"Error: {error['error_msg']}")
        if errmsgs:
            await self._send_msg("\n".join(errmsgs))

        message = f"Blacklist contains {blacklist['length']} pairs\n"
        message += f"`{', '.join(blacklist['blacklist'])}`"

        logger.debug(message)
        await self._send_msg(message)

    @authorized_only
    async def _blacklist_delete(self, update: Update, context: CallbackContext) -> None:
        """
        Handler for /bl_delete
        Deletes pair(s) from current blacklist
        """
        await self.send_blacklist_msg(self._rpc._rpc_blacklist_delete(context.args or []))

    @authorized_only
    async def _logs(self, update: Update, context: CallbackContext) -> None:
        """
        Handler for /logs
        Shows the latest logs
        """
        try:
            limit = int(context.args[0]) if context.args else 10
        except (TypeError, ValueError, IndexError):
            limit = 10
        logs = RPC._rpc_get_logs(limit)["logs"]
        msgs = ""
        msg_template = "*{}* {}: {} \\- `{}`"
        for logrec in logs:
            msg = msg_template.format(
                escape_markdown(logrec[0], version=2),
                escape_markdown(logrec[2], version=2),
                escape_markdown(logrec[3], version=2),
                escape_markdown(logrec[4], version=2),
            )
            if len(msgs + msg) + 10 >= MAX_MESSAGE_LENGTH:
                # Send message immediately if it would become too long
                await self._send_msg(msgs, parse_mode=ParseMode.MARKDOWN_V2)
                msgs = msg + "\n"
            else:
                # Append message to messages to send
                msgs += msg + "\n"

        if msgs:
            await self._send_msg(msgs, parse_mode=ParseMode.MARKDOWN_V2)

    @authorized_only
    async def _help(self, update: Update, context: CallbackContext) -> None:
        """
        Handler for /help.
        Show commands of the bot
        :param bot: telegram bot
        :param update: message update
        :return: None
        """
        force_enter_text = (
            "*/forcelong <pair> [<rate>]:* `Instantly buys the given pair. "
            "Optionally takes a rate at which to buy "
            "(only applies to limit orders).` \n"
        )
        if self._rpc._freqtrade.trading_mode != TradingMode.SPOT:
            force_enter_text += (
                "*/forceshort <pair> [<rate>]:* `Instantly shorts the given pair. "
                "Optionally takes a rate at which to sell "
                "(only applies to limit orders).` \n"
            )
        message = (
            "_Bot Control_\n"
            "------------\n"
            "*/start:* `Starts the trader`\n"
            "*/pause:* `Pause the new entries for trader, but handles open trades gracefully`\n"
            "*/stop:* `Stops the trader`\n"
            "*/stopentry:* `Stops entering, but handles open trades gracefully` \n"
            "*/forceexit <trade_id>|all:* `Instantly exits the given trade or all trades, "
            "regardless of profit`\n"
            "*/fx <trade_id>|all:* `Alias to /forceexit`\n"
            f"{force_enter_text if self._config.get('force_entry_enable', False) else ''}"
            "*/delete <trade_id>:* `Instantly delete the given trade in the database`\n"
            "*/reload_trade <trade_id>:* `Reload trade from exchange Orders`\n"
            "*/cancel_open_order <trade_id>:* `Cancels open orders for trade. "
            "Only valid when the trade has open orders.`\n"
            "*/coo <trade_id>|all:* `Alias to /cancel_open_order`\n"
            "*/whitelist [sorted] [baseonly]:* `Show current whitelist. Optionally in "
            "order and/or only displaying the base currency of each pairing.`\n"
            "*/blacklist [pair]:* `Show current blacklist, or adds one or more pairs "
            "to the blacklist.` \n"
            "*/blacklist_delete [pairs]| /bl_delete [pairs]:* "
            "`Delete pair / pattern from blacklist. Will reset on reload_conf.` \n"
            "*/reload_config:* `Reload configuration file` \n"
            "*/unlock <pair|id>:* `Unlock this Pair (or this lock id if it's numeric)`\n"
            "_Current state_\n"
            "------------\n"
            "*/show_config:* `Show running configuration` \n"
            "*/locks:* `Show currently locked pairs`\n"
            "*/balance:* `Show bot managed balance per currency`\n"
            "*/balance total:* `Show account balance per currency`\n"
            "*/logs [limit]:* `Show latest logs - defaults to 10` \n"
            "*/count:* `Show number of active trades compared to allowed number of trades`\n"
            "*/health* `Show latest process timestamp - defaults to 1970-01-01 00:00:00` \n"
            "*/marketdir [long | short | even | none]:* `Updates the user managed variable "
            "that represents the current market direction. If no direction is provided `"
            "`the currently set market direction will be output.` \n"
            "*/list_custom_data <trade_id> <key>:* `List custom_data for Trade ID & Key combo.`\n"
            "`If no Key is supplied it will list all key-value pairs found for that Trade ID.`\n"
            "_Statistics_\n"
            "------------\n"
            "*/status <trade_id>|[table]:* `Lists all open trades`\n"
            "         *<trade_id> :* `Lists one or more specific trades.`\n"
            "                        `Separate multiple <trade_id> with a blank space.`\n"
            "         *table :* `will display trades in a table`\n"
            "                `pending buy orders are marked with an asterisk (*)`\n"
            "                `pending sell orders are marked with a double asterisk (**)`\n"
            "*/entries <pair|none>:* `Shows the enter_tag performance`\n"
            "*/exits <pair|none>:* `Shows the exit reason performance`\n"
            "*/mix_tags <pair|none>:* `Shows combined entry tag + exit reason performance`\n"
            "*/trades [limit]:* `Lists last closed trades (limited to 10 by default)`\n"
            "*/profit [<n>]:* `Lists cumulative profit from all finished trades, "
            "over the last n days`\n"
            "*/profit_long [<n>]:* `Lists cumulative profit from all finished long trades, "
            "over the last n days`\n"
            "*/profit_short [<n>]:* `Lists cumulative profit from all finished short trades, "
            "over the last n days`\n"
            "*/performance:* `Show performance of each finished trade grouped by pair`\n"
            "*/daily <n>:* `Shows profit or loss per day, over the last n days`\n"
            "*/weekly <n>:* `Shows statistics per week, over the last n weeks`\n"
            "*/monthly <n>:* `Shows statistics per month, over the last n months`\n"
            "*/stats:* `Shows Wins / losses by Sell reason as well as "
            "Avg. holding durations for buys and sells.`\n"
            "*/help:* `This help message`\n"
            "*/version:* `Show version`\n"
        )

        await self._send_msg(message, parse_mode=ParseMode.MARKDOWN)

    @authorized_only
    async def _health(self, update: Update, context: CallbackContext) -> None:
        """
        Handler for /health
        Shows the last process timestamp
        """
        health = self._rpc.health()
        message = f"Last process: `{health['last_process_loc']}`\n"
        message += f"Initial bot start: `{health['bot_start_loc']}`\n"
        message += f"Last bot restart: `{health['bot_startup_loc']}`"
        await self._send_msg(message)

    @authorized_only
    async def _version(self, update: Update, context: CallbackContext) -> None:
        """
        Handler for /version.
        Show version information
        :param bot: telegram bot
        :param update: message update
        :return: None
        """
        strategy_version = self._rpc._freqtrade.strategy.version()
        version_string = f"*Version:* `{__version__}`"
        if strategy_version is not None:
            version_string += f"\n*Strategy version: * `{strategy_version}`"

        await self._send_msg(version_string)

    @authorized_only
    async def _show_config(self, update: Update, context: CallbackContext) -> None:
        """
        Handler for /show_config.
        Show config information information
        :param bot: telegram bot
        :param update: message update
        :return: None
        """
        val = RPC._rpc_show_config(self._config, self._rpc._freqtrade.state)

        if val["trailing_stop"]:
            sl_info = (
                f"*Initial Stoploss:* `{val['stoploss']}`\n"
                f"*Trailing stop positive:* `{val['trailing_stop_positive']}`\n"
                f"*Trailing stop offset:* `{val['trailing_stop_positive_offset']}`\n"
                f"*Only trail above offset:* `{val['trailing_only_offset_is_reached']}`\n"
            )

        else:
            sl_info = f"*Stoploss:* `{val['stoploss']}`\n"

        if val["position_adjustment_enable"]:
            pa_info = (
                f"*Position adjustment:* On\n"
                f"*Max enter position adjustment:* `{val['max_entry_position_adjustment']}`\n"
            )
        else:
            pa_info = "*Position adjustment:* Off\n"

        await self._send_msg(
            f"*Mode:* `{'Dry-run' if val['dry_run'] else 'Live'}`\n"
            f"*Exchange:* `{val['exchange']}`\n"
            f"*Market: * `{val['trading_mode']}`\n"
            f"*Stake per trade:* `{val['stake_amount']} {val['stake_currency']}`\n"
            f"*Max open Trades:* `{val['max_open_trades']}`\n"
            f"*Minimum ROI:* `{val['minimal_roi']}`\n"
            f"*Entry strategy:* ```\n{json.dumps(val['entry_pricing'])}```\n"
            f"*Exit strategy:* ```\n{json.dumps(val['exit_pricing'])}```\n"
            f"{sl_info}"
            f"{pa_info}"
            f"*Timeframe:* `{val['timeframe']}`\n"
            f"*Strategy:* `{val['strategy']}`\n"
            f"*Current state:* `{val['state']}`"
        )

    @authorized_only
    async def _list_custom_data(self, update: Update, context: CallbackContext) -> None:
        """
        Handler for /list_custom_data <id> <key>.
        List custom_data for specified trade (and key if supplied).
        :param bot: telegram bot
        :param update: message update
        :return: None
        """
        try:
            if not context.args or len(context.args) == 0:
                raise RPCException("Trade-id not set.")
            trade_id = int(context.args[0])
            key = None if len(context.args) < 2 else str(context.args[1])

            results = self._rpc._rpc_list_custom_data(trade_id, key)
            messages = []
            if len(results) > 0:
                trade_custom_data = results[0]["custom_data"]
                messages.append(
                    "Found custom-data entr" + ("ies: " if len(trade_custom_data) > 1 else "y: ")
                )
                for custom_data in trade_custom_data:
                    lines = [
                        f"*Key:* `{custom_data['key']}`",
                        f"*Type:* `{custom_data['type']}`",
                        f"*Value:* `{custom_data['value']}`",
                        f"*Create Date:* `{format_date(custom_data['created_at'])}`",
                        f"*Update Date:* `{format_date(custom_data['updated_at'])}`",
                    ]
                    # Filter empty lines using list-comprehension
                    messages.append("\n".join([line for line in lines if line]))
                for msg in messages:
                    if len(msg) > MAX_MESSAGE_LENGTH:
                        msg = "Message dropped because length exceeds "
                        msg += f"maximum allowed characters: {MAX_MESSAGE_LENGTH}"
                        logger.warning(msg)
                    await self._send_msg(msg)
            else:
                message = f"Didn't find any custom-data entries for Trade ID: `{trade_id}`"
                message += f" and Key: `{key}`." if key is not None else ""
                await self._send_msg(message)

        except RPCException as e:
            await self._send_msg(str(e))

    async def _update_msg(
        self,
        query: CallbackQuery,
        msg: str,
        callback_path: str = "",
        reload_able: bool = False,
        parse_mode: str = ParseMode.MARKDOWN,
    ) -> None:
        if reload_able:
            reply_markup = InlineKeyboardMarkup(
                [
                    [InlineKeyboardButton("Refresh", callback_data=callback_path)],
                ]
            )
        else:
            reply_markup = InlineKeyboardMarkup([[]])
        msg += f"\nUpdated: {datetime.now().ctime()}"
        if not query.message:
            return

        try:
            await query.edit_message_text(
                text=msg, parse_mode=parse_mode, reply_markup=reply_markup
            )
        except BadRequest as e:
            if "not modified" in e.message.lower():
                pass
            else:
                logger.warning("TelegramError: %s", e.message)
        except TelegramError as telegram_err:
            logger.warning("TelegramError: %s! Giving up on that message.", telegram_err.message)

    async def _send_msg(
        self,
        msg: str,
        parse_mode: str = ParseMode.MARKDOWN,
        disable_notification: bool = False,
        keyboard: list[list[InlineKeyboardButton]] | None = None,
        callback_path: str = "",
        reload_able: bool = False,
        query: CallbackQuery | None = None,
    ) -> None:
        """
        Send given markdown message
        :param msg: message
        :param bot: alternative bot
        :param parse_mode: telegram parse mode
        :return: None
        """
        reply_markup: InlineKeyboardMarkup | ReplyKeyboardMarkup
        if query:
            await self._update_msg(
                query=query,
                msg=msg,
                parse_mode=parse_mode,
                callback_path=callback_path,
                reload_able=reload_able,
            )
            return
        if reload_able and self._config["telegram"].get("reload", True):
            reply_markup = InlineKeyboardMarkup(
                [[InlineKeyboardButton("Refresh", callback_data=callback_path)]]
            )
        else:
            if keyboard is not None:
                reply_markup = InlineKeyboardMarkup(keyboard)
            else:
                reply_markup = ReplyKeyboardMarkup(self._keyboard, resize_keyboard=True)
        try:
            try:
                await self._app.bot.send_message(
                    self._config["telegram"]["chat_id"],
                    text=msg,
                    parse_mode=parse_mode,
                    reply_markup=reply_markup,
                    disable_notification=disable_notification,
                    message_thread_id=self._config["telegram"].get("topic_id"),
                )
            except NetworkError as network_err:
                # Sometimes the telegram server resets the current connection,
                # if this is the case we send the message again.
                logger.warning(
                    "Telegram NetworkError: %s! Trying one more time.", network_err.message
                )
                await self._app.bot.send_message(
                    self._config["telegram"]["chat_id"],
                    text=msg,
                    parse_mode=parse_mode,
                    reply_markup=reply_markup,
                    disable_notification=disable_notification,
                    message_thread_id=self._config["telegram"].get("topic_id"),
                )
        except TelegramError as telegram_err:
            logger.warning("TelegramError: %s! Giving up on that message.", telegram_err.message)

    @authorized_only
    async def _changemarketdir(self, update: Update, context: CallbackContext) -> None:
        """
        Handler for /marketdir.
        Updates the bot's market_direction
        :param bot: telegram bot
        :param update: message update
        :return: None
        """
        if context.args and len(context.args) == 1:
            new_market_dir_arg = context.args[0]
            old_market_dir = self._rpc._get_market_direction()
            new_market_dir = None
            if new_market_dir_arg == "long":
                new_market_dir = MarketDirection.LONG
            elif new_market_dir_arg == "short":
                new_market_dir = MarketDirection.SHORT
            elif new_market_dir_arg == "even":
                new_market_dir = MarketDirection.EVEN
            elif new_market_dir_arg == "none":
                new_market_dir = MarketDirection.NONE

            if new_market_dir is not None:
                self._rpc._update_market_direction(new_market_dir)
                await self._send_msg(
                    "Successfully updated market direction"
                    f" from *{old_market_dir}* to *{new_market_dir}*."
                )
            else:
                raise RPCException(
                    "Invalid market direction provided. \n"
                    "Valid market directions: *long, short, even, none*"
                )
        elif context.args is not None and len(context.args) == 0:
            old_market_dir = self._rpc._get_market_direction()
            await self._send_msg(f"Currently set market direction: *{old_market_dir}*")
        else:
            raise RPCException(
                "Invalid usage of command /marketdir. \n"
                "Usage: */marketdir [short |  long | even | none]*"
            )

    async def _tg_info(self, update: Update, context: CallbackContext) -> None:
        """
        Intentionally unauthenticated Handler for /tg_info.
        Returns information about the current telegram chat - even if chat_id does not
        correspond to this chat.

        :param update: message update
        :return: None
        """
        if not update.message:
            return
        chat_id = update.message.chat_id
        topic_id = update.message.message_thread_id
        user_id = (
            update.effective_user.id if topic_id is not None and update.effective_user else None
        )

        msg = f"""Freqtrade Bot Info:
        ```json
            {{
                "enabled": true,
                "token": "********",
                "chat_id": "{chat_id}",
                {f'"topic_id": "{topic_id}",' if topic_id else ""}
                {f'//"authorized_users": ["{user_id}"]' if topic_id and user_id else ""}
            }}
        ```
        """
        try:
            await context.bot.send_message(
                chat_id=chat_id,
                text=msg,
                parse_mode=ParseMode.MARKDOWN_V2,
                message_thread_id=topic_id,
            )
        except TelegramError as telegram_err:
            logger.warning("TelegramError: %s! Giving up on that message.", telegram_err.message)

    @authorized_only
    async def _custom_backtesting(self, update, context):
        """대화형 백테스팅: 전략 선택 단계"""
        try:
            user_id = update.effective_user.id
            chat_id = update.effective_chat.id
            
            # 전략 목록 가져오기
            from freqtrade.resolvers.strategy_resolver import StrategyResolver
            
            strategies = StrategyResolver.search_all_objects(
                self._config, False, self._config.get("recursive_strategy_search", False)
            )
            # 이름만 추출 후 정렬
            strategies = sorted([s["name"] for s in strategies], key=str.lower)
            # 중복 제거 (순서 유지)
            strategies = list(dict.fromkeys(strategies))
            # SampleStrategy 제거 (기본 샘플 전략이므로)
            strategies = [s for s in strategies if s != "SampleStrategy"]
            # 화면 가독성을 위해 Counter류 전략을 맨 뒤로 이동
            tail = [s for s in strategies if "counter" in s.lower()]
            head = [s for s in strategies if "counter" not in s.lower()]
            strategies = head + tail
            
            if not strategies:
                await update.message.reply_text("❌ 사용 가능한 전략이 없습니다.")
                return
            
            # 전략 버튼 생성
            strategy_buttons = [
                InlineKeyboardButton(text=name, callback_data=f"backtest_strategy__{name}")
                for name in strategies
            ]
            buttons_aligned = self._layout_inline_keyboard(strategy_buttons, cols=2)
            buttons_aligned.append([InlineKeyboardButton(text="취소", callback_data="backtest_cancel")])
            
            # 전략 선택 메시지 전송
            msg = await update.message.reply_text(
                "📊 사용하실 전략을 선택해주세요",
                reply_markup=InlineKeyboardMarkup(buttons_aligned)
            )
            
            # 상태 초기화
            self._backtesting_state[user_id] = {
                'step': 'strategy',
                'strategy': None,
                'start_date': None,
                'end_date': None,
                # 취소 시 함께 지울 메시지들 (사용자 명령 포함)
                'messages': [msg.message_id, update.message.message_id]
            }
                
        except Exception as e:
            logger.error(f"Error in backtesting init: {e}")
            await update.message.reply_text(f"❌ 오류: {str(e)[:200]}")

    @authorized_only
    async def _backtesting_strategy_callback(self, update: Update, context: CallbackContext) -> None:
        """전략 선택 콜백 핸들러"""
        try:
            query = update.callback_query
            await query.answer()
            
            user_id = update.effective_user.id
            chat_id = update.effective_chat.id
            
            if query.data == "backtest_cancel":
                # 취소 처리
                if user_id in self._backtesting_state:
                    # 이전 메시지들 삭제
                    for msg_id in self._backtesting_state[user_id].get('messages', []):
                        try:
                            await context.bot.delete_message(chat_id=chat_id, message_id=msg_id)
                        except Exception:
                            pass
                    del self._backtesting_state[user_id]
                await query.edit_message_text("❌ 백테스팅이 취소되었습니다.")
                return
            
            # 전략 이름 추출
            if not query.data.startswith("backtest_strategy__"):
                return
            
            strategy_name = query.data.replace("backtest_strategy__", "")
            
            # 상태 업데이트
            if user_id not in self._backtesting_state:
                await query.edit_message_text("❌ 세션이 만료되었습니다. /backtesting을 다시 입력해주세요.")
                return
            
            self._backtesting_state[user_id]['strategy'] = strategy_name
            self._backtesting_state[user_id]['step'] = 'start_date'
            
            # 전략 선택 메시지 삭제
            try:
                await context.bot.delete_message(chat_id=chat_id, message_id=query.message.message_id)
            except Exception:
                pass
            
            # 시작날짜 입력 요청
            msg = await context.bot.send_message(
                chat_id=chat_id,
                text=f"📅 시작날짜를 입력해주세요\n\n전략: {strategy_name}\n형식: YYYY-MM-DD (예: 2024-01-01)"
            )
            self._backtesting_state[user_id]['messages'].append(msg.message_id)
                
        except Exception as e:
            logger.error(f"Error in backtesting strategy callback: {e}")
            try:
                await query.edit_message_text(f"❌ 오류: {str(e)[:200]}")
            except Exception:
                pass

    @authorized_only
    async def _backtesting_message_handler(self, update: Update, context: CallbackContext) -> None:
        """백테스팅 날짜 입력 메시지 핸들러"""
        try:
            user_id = update.effective_user.id
            chat_id = update.effective_chat.id
            
            # 백테스팅 상태에 있는 사용자인지 확인
            if user_id not in self._backtesting_state:
                return
            
            state = self._backtesting_state[user_id]
            step = state['step']
            text = update.message.text.strip()
            
            # 날짜 형식 검증
            import re
            date_pattern = r'^\d{4}-\d{2}-\d{2}$'
            if not re.match(date_pattern, text):
                msg = await update.message.reply_text("❌ 날짜 형식이 올바르지 않습니다. YYYY-MM-DD 형식으로 입력해주세요. (예: 2024-01-01)")
                self._backtesting_state[user_id]['messages'].append(msg.message_id)
                return
            
            # 명령어 메시지 삭제
            try:
                await context.bot.delete_message(chat_id=chat_id, message_id=update.message.message_id)
            except Exception:
                pass
            
            if step == 'start_date':
                # 시작날짜 입력 완료
                state['start_date'] = text
                state['step'] = 'end_date'
                
                # 시작날짜 요청 메시지 삭제
                if state['messages']:
                    try:
                        await context.bot.delete_message(chat_id=chat_id, message_id=state['messages'][-1])
                        state['messages'] = state['messages'][:-1]
                    except Exception:
                        pass
                
                # 종료날짜 입력 요청
                msg = await context.bot.send_message(
                    chat_id=chat_id,
                    text=f"📅 종료날짜를 입력해주세요\n\n전략: {state['strategy']}\n시작일: {text}\n형식: YYYY-MM-DD (예: 2024-12-31)"
                )
                state['messages'].append(msg.message_id)
                
            elif step == 'end_date':
                # 종료날짜 입력 완료
                state['end_date'] = text
                
                # 종료날짜 요청 메시지 삭제
                if state['messages']:
                    try:
                        await context.bot.delete_message(chat_id=chat_id, message_id=state['messages'][-1])
                        state['messages'] = state['messages'][:-1]
                    except Exception:
                        pass
                
                # 백테스팅 실행
                strategy_name = state['strategy']
                start_date = state['start_date']
                end_date = state['end_date']
                
                # 실행 메시지
                exec_msg = await context.bot.send_message(
                    chat_id=chat_id,
                    text=f"🚀 백테스팅 시작: {strategy_name} ({start_date} ~ {end_date})"
                )
                state['messages'].append(exec_msg.message_id)
                
                # 백테스팅 실행
                import subprocess
                start_date_formatted = start_date.replace("-", "")
                end_date_formatted = end_date.replace("-", "")
                
                cmd = [
                    "freqtrade", "backtesting",
                    "--strategy", strategy_name,
                    "--timerange", f"{start_date_formatted}-{end_date_formatted}",
                    "--config", "user_data/config.json"
                ]
                result = subprocess.run(
                    cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    universal_newlines=True, cwd="/freqtrade"
                )
                
                # 실행 메시지 삭제
                try:
                    await context.bot.delete_message(chat_id=chat_id, message_id=exec_msg.message_id)
                except Exception:
                    pass
                
                if result.returncode == 0:
                    # 결과 파싱
                    output = result.stdout
                    if result.stderr:
                        output = f"{output}\n{result.stderr}" if output else result.stderr
                    lines = output.split("\n")
                    summary = []
                    for raw_line in lines:
                        if (
                            "Total/Daily Avg Trades" in raw_line
                            or "Starting balance" in raw_line
                            or "Final balance" in raw_line
                            or "Total profit %" in raw_line
                        ):
                            parts = [p.strip() for p in re.split(r"[│|]", raw_line) if p.strip()]
                            if len(parts) >= 2:
                                metric = parts[0]
                                value = parts[1]
                                # Best trade와 Worst trade 제외
                                if "Best trade" not in metric and "Worst trade" not in metric:
                                    summary.append(f"• {metric}: {value}")
                            else:
                                cleaned = raw_line.strip().rstrip("|").rstrip("│")
                                # Best trade와 Worst trade 제외
                                if "Best trade" not in cleaned and "Worst trade" not in cleaned:
                                    summary.append(f"• {cleaned}")
                    
                    # 메시지 구성: 백테스팅 완료 문구를 가장 위에 배치
                    header = f"✅ 백테스팅 완료\n\n📊 전략: {strategy_name}\n📅 기간: {start_date} ~ {end_date}\n"
                    
                    if summary:
                        summary_text = "\n".join(summary)
                        result_msg = f"{header}📊 결과 요약\n{summary_text}"
                    else:
                        result_msg = header.rstrip()
                    
                    await context.bot.send_message(chat_id=chat_id, text=result_msg)
                else:
                    error_msg = f"❌ 백테스팅 실패: {result.stderr[:200]}"
                    await context.bot.send_message(chat_id=chat_id, text=error_msg)
                
                # 상태 초기화
                del self._backtesting_state[user_id]
        except Exception as e:
            logger.error(f"Error in backtesting message handler: {e}")
            if user_id in self._backtesting_state:
                try:
                    await context.bot.send_message(chat_id=chat_id, text=f"❌ 오류: {str(e)[:200]}")
                    # 상태 초기화
                    for msg_id in self._backtesting_state[user_id].get('messages', []):
                        try:
                            await context.bot.delete_message(chat_id=chat_id, message_id=msg_id)
                        except Exception:
                            pass
                    del self._backtesting_state[user_id]
                except Exception:
                    pass

    @authorized_only
    async def _position(self, update: Update, context: CallbackContext) -> None:
        """
        현재 유지 중인 포지션을 보여주는 커스텀 명령어
        """
        try:
            # 열린 포지션 조회
            try:
                open_trades = self._rpc._rpc_trade_status()
            except RPCException:
                # 열린 포지션이 없는 경우
                await update.message.reply_text("📊 현재 열린 포지션이 없습니다.")
                return
            
            if not open_trades or len(open_trades) == 0:
                await update.message.reply_text("📊 현재 열린 포지션이 없습니다.")
                return

            # 포지션 정보 포맷팅
            messages = []
            kst = timezone(timedelta(hours=9))
            
            for i, trade in enumerate(open_trades, 1):
                pair = trade.get("pair", "-")
                # Pair를 BTCUSDT 형식으로 변환 (BTC/USDT:USDT -> BTCUSDT)
                pair_display = pair.replace("/", "").replace(":USDT", "")
                
                trade_id = trade.get("trade_id", trade.get("id", "-"))
                is_short = trade.get("is_short", False)
                direction = "SHORT" if is_short else "LONG"
                leverage = trade.get("leverage")
                dir_txt = f"{direction}{f' ({leverage}x)' if leverage else ''}"
                
                open_rate = trade.get("open_rate", 0.0)
                current_rate = trade.get("current_rate", open_rate)
                profit_ratio = trade.get("profit_ratio", 0.0) or 0.0
                profit_abs = trade.get("profit_abs", 0.0) or 0.0
                amount = trade.get("amount", 0.0)
                stake_amount = trade.get("stake_amount", 0.0)
                quote_currency = trade.get("quote_currency", self._config.get("stake_currency", ""))
                
                # 진입시각을 한국 시간으로 변환
                open_date = trade.get("open_date")
                open_date_kst_str = "-"
                if open_date:
                    try:
                        if isinstance(open_date, str):
                            open_date_dt = datetime.fromisoformat(open_date.replace('Z', '+00:00'))
                        elif isinstance(open_date, datetime):
                            open_date_dt = open_date
                        else:
                            open_date_dt = datetime.fromtimestamp(open_date)
                        
                        # UTC로 변환 후 KST로 변환
                        if open_date_dt.tzinfo is None:
                            open_date_dt = open_date_dt.replace(tzinfo=timezone.utc)
                        else:
                            open_date_dt = open_date_dt.astimezone(timezone.utc)
                        open_date_kst = open_date_dt.astimezone(kst)
                        open_date_kst_str = open_date_kst.strftime("%Y-%m-%d %H:%M:%S")
                    except Exception:
                        open_date_kst_str = str(open_date)
                
                # 수량 포맷팅: 필요없는 0 제거하고 BTC 단위 붙이기
                amount_str = f"{amount:.8f}".rstrip('0').rstrip('.')
                if amount_str == "0":
                    amount_str = "0"
                amount_display = f"{amount_str} BTC"
                
                profit_pct = profit_ratio * 100
                profit_emoji = "📈" if profit_abs >= 0 else "📉"
                profit_sign = "+" if profit_abs >= 0 else ""
                
                # 포지션 정보 메시지 생성
                direction_header = "🔴 *숏 포지션*" if is_short else "🟢 *롱 포지션*"
                msg = f"{direction_header}\n\n"
                msg += f"*Trade ID:* `{trade_id}`\n"
                msg += f"*페어:* `{pair_display}`\n"
                msg += f"*방향:* `{dir_txt}`\n"
                msg += f"*수량:* `{amount_display}`\n"
                msg += f"*투자액:* `{fmt_coin(stake_amount, quote_currency)}`\n"
                msg += f"*진입가:* `{round_value(open_rate, 8)}`\n"
                msg += f"*현재가:* `{round_value(current_rate, 8)}`\n"
                msg += f"*수익률:* {profit_emoji} `{profit_sign}{profit_pct:.2f}%` `({profit_sign}{fmt_coin(profit_abs, quote_currency)})`\n"
                msg += f"*진입시각:* `{open_date_kst_str}`\n"
                
                # Stoploss 정보 추가
                stop_loss_abs = trade.get("stop_loss_abs")
                if stop_loss_abs:
                    stop_loss_ratio = trade.get("stop_loss_ratio", 0.0) or 0.0
                    msg += f"*스탑로스:* `{round_value(stop_loss_abs, 8)}` ({stop_loss_ratio:.2%})\n"
                
                messages.append(msg)

            # 모든 포지션 메시지 전송 (차트확인 버튼 포함)
            chart_button = InlineKeyboardButton(text="차트확인", url="http://127.0.0.1:8081")
            keyboard = InlineKeyboardMarkup([[chart_button]])
            
            for msg in messages:
                await update.message.reply_text(
                    msg, 
                    parse_mode=ParseMode.MARKDOWN,
                    reply_markup=keyboard
                )
                
        except Exception as e:
            logger.error(f"Error in position command: {e}")
            await update.message.reply_text(f"❌ 오류: {str(e)[:200]}")

    @authorized_only
    async def _signal(self, update: Update, context: CallbackContext) -> None:
        """
        현재 전략 기반으로 진입 신호를 분석하여 보여주는 커스텀 명령어
        """
        try:
            # 전략 및 데이터프로바이더 접근
            freqtrade = self._rpc._freqtrade
            strategy = freqtrade.strategy
            dataprovider = freqtrade.dataprovider
            exchange = freqtrade.exchange

            # 현재 전략 정보 가져오기
            strategy_name = self._config.get("strategy", "-")
            timeframe = getattr(strategy, "timeframe", self._config.get("timeframe", "5m"))
            tf_4h = "4h"
            chart_base_url = "http://127.0.0.1:8081/graph"

            # 화이트리스트 페어 가져오기
            try:
                whitelist_data = self._rpc._rpc_whitelist()
                whitelist = whitelist_data.get("whitelist", [])
            except Exception:
                await update.message.reply_text("❌ 화이트리스트를 가져올 수 없습니다.")
                return

            if not whitelist:
                await update.message.reply_text("📊 화이트리스트에 등록된 페어가 없습니다.")
                return

            messages = []

            # 각 페어에 대해 분석
            for pair in whitelist:
                try:
                    # 분석된 데이터프레임 가져오기
                    analyzed_df, last_analyzed = dataprovider.get_analyzed_dataframe(pair, timeframe)

                    if analyzed_df.empty or len(analyzed_df) == 0:
                        messages.append((f"📊 *{pair}*\n\n데이터가 없습니다.\n", None))
                        continue

                    # 현재 가격 가져오기
                    try:
                        current_rate = exchange.get_rate(pair, True, "entry", False)
                    except Exception:
                        try:
                            ticker = exchange.fetch_ticker(pair)
                            current_rate = ticker.get("last", 0.0)
                        except Exception:
                            current_rate = 0.0

                    # 진입 신호 확인
                    signal, enter_tag = strategy.get_entry_signal(pair, timeframe, analyzed_df)

                    # 최신 캔들 정보 가져오기
                    latest_candle = analyzed_df.iloc[-1] if len(analyzed_df) > 0 else None

                    if latest_candle is None:
                        messages.append((f"📊 *{pair}*\n\n캔들 데이터가 없습니다.\n", None))
                        continue

                    # 메시지 생성
                    # pair에서 :USDT 제거 (예: BTC/USDT:USDT -> BTC/USDT)
                    display_pair = pair.replace(":USDT", "") if pair.endswith(":USDT") else pair
                    msg = f"📊 *{display_pair} 분석*\n\n"
                    msg += f"*사용전략:* `{strategy_name}`\n"
                    msg += f"\n*현재가:* `{round_value(current_rate, 8)}`\n"

                    # 4시간봉 데이터 가져오기 및 지표 계산
                    import numpy as np  # noqa: WPS433
                    try:
                        import talib.abstract as ta
                        from freqtrade.vendor.qtpylib.indicators import bollinger_bands, typical_price
                        ta_available = True
                    except ImportError:
                        ta_available = False
                        ta = None
                        bollinger_bands = None
                        typical_price = None

                    df_4h = None
                    rsi_4h = None
                    bb_lower_4h = None
                    bb_mid_4h = None
                    bb_upper_4h = None
                    macd_4h = None
                    macdsignal_4h = None
                    macdhist_4h = None
                    adx_4h = None
                    trend_slope = None
                    trend_direction = None
                    has_4h_data = False

                    if ta_available:
                        try:
                            df_4h = dataprovider.ohlcv(pair, tf_4h)
                        except Exception:
                            df_4h = None

                        if df_4h is None or getattr(df_4h, "empty", True):
                            try:
                                df_4h = dataprovider.get_pair_dataframe(pair, tf_4h)
                            except Exception:
                                df_4h = None

                        if (df_4h is None or getattr(df_4h, "empty", True)) and exchange and hasattr(exchange, "_api") and hasattr(exchange._api, "fetch_ohlcv"):
                            try:
                                ohlcv_data = exchange._api.fetch_ohlcv(pair, tf_4h, limit=100)
                                if ohlcv_data:
                                    from freqtrade.data.converter import ohlcv_to_dataframe
                                    df_4h = ohlcv_to_dataframe(ohlcv_data, tf_4h, pair, fill_missing=True, drop_incomplete=False)
                            except Exception as fetch_error:
                                logger.debug(f"Could not fetch 4h data from exchange for {pair}: {fetch_error}")

                        if df_4h is not None and not getattr(df_4h, "empty", True) and len(df_4h) > 20:
                            has_4h_data = True
                            rsi_4h = ta.RSI(df_4h, timeperiod=14)

                            bb = bollinger_bands(typical_price(df_4h), window=20, stds=2)
                            bb_lower_4h = bb["lower"]
                            bb_mid_4h = bb["mid"]
                            bb_upper_4h = bb["upper"]

                            # MACD 계산
                            macd_result = ta.MACD(df_4h)
                            macd_4h = macd_result["macd"]
                            macdsignal_4h = macd_result["macdsignal"]
                            macdhist_4h = macd_result["macdhist"]

                            # ADX 계산
                            adx_4h = ta.ADX(df_4h)

                            recent_closes = df_4h["close"].tail(20).values
                            if len(recent_closes) >= 10:
                                x_axis = np.arange(len(recent_closes))
                                slope = np.polyfit(x_axis, recent_closes, 1)[0]
                                trend_slope = slope
                                current_price_val = recent_closes[-1]
                                if current_price_val > 0:
                                    slope_pct = (slope / current_price_val) * 100
                                    if slope_pct > 0.1:
                                        trend_direction = "📈 상승 추세"
                                    elif slope_pct < -0.1:
                                        trend_direction = "📉 하락 추세"
                                    else:
                                        trend_direction = "➡️ 횡보"
                        else:
                            has_4h_data = False

                    # 지표 값 수집
                    rsi_value = None
                    bb_position = None
                    macd_val = None
                    macdsignal_val = None
                    macdhist_val = None
                    adx_val = None
                    
                    if has_4h_data and rsi_4h is not None and len(rsi_4h) > 0:
                        rsi_value = rsi_4h.iloc[-1] if not isnan(rsi_4h.iloc[-1]) else None
                    
                    if has_4h_data and bb_lower_4h is not None and bb_upper_4h is not None and len(bb_lower_4h) > 0:
                        bb_lower = bb_lower_4h.iloc[-1]
                        bb_upper = bb_upper_4h.iloc[-1]
                        if not (isnan(bb_lower) or isnan(bb_upper)) and current_rate > 0 and bb_upper != bb_lower:
                            bb_position = ((current_rate - bb_lower) / (bb_upper - bb_lower)) * 100
                    
                    if has_4h_data and macd_4h is not None and macdsignal_4h is not None and macdhist_4h is not None and len(macd_4h) > 0:
                        macd_val = macd_4h.iloc[-1] if not isnan(macd_4h.iloc[-1]) else None
                        macdsignal_val = macdsignal_4h.iloc[-1] if not isnan(macdsignal_4h.iloc[-1]) else None
                        macdhist_val = macdhist_4h.iloc[-1] if not isnan(macdhist_4h.iloc[-1]) else None
                    
                    if has_4h_data and adx_4h is not None and len(adx_4h) > 0:
                        adx_val = adx_4h.iloc[-1] if not isnan(adx_4h.iloc[-1]) else None
                    
                    # 동적 분석 메시지 생성
                    indicator_info = []
                    
                    if rsi_value is not None:
                        indicator_info.append(f"RSI: `{rsi_value:.2f}`")
                        
                        # RSI 동적 해석
                        if rsi_value < 20:
                            indicator_info.append(f"  → 극단적인 과매도 상태입니다. 강한 반등 신호로 볼 수 있습니다.\n")
                        elif rsi_value < 30:
                            indicator_info.append(f"  → 과매도 구간에 진입했습니다. 롱 포지션 진입 타이밍을 고려해보세요.\n")
                        elif rsi_value < 40:
                            indicator_info.append(f"  → 매도 압력이 우세한 상황입니다. 추가 하락 여지가 있습니다.\n")
                        elif rsi_value < 60:
                            indicator_info.append(f"  → 중립 구간입니다. 뚜렷한 방향성이 보이지 않네요.\n")
                        elif rsi_value < 70:
                            indicator_info.append(f"  → 매수 압력이 강해지고 있습니다. 상승 모멘텀이 형성 중입니다.\n")
                        elif rsi_value < 80:
                            indicator_info.append(f"  → 과매수 구간입니다. 단기 조정 가능성을 염두에 두세요.\n")
                        else:
                            indicator_info.append(f"  → 극단적 과열 상태입니다. 급격한 조정에 주의하세요.\n")
                    
                    if bb_position is not None and has_4h_data:
                        bb_lower = bb_lower_4h.iloc[-1]
                        bb_mid_val = bb_mid_4h.iloc[-1] if bb_mid_4h is not None and len(bb_mid_4h) > 0 else (bb_lower + bb_upper_4h.iloc[-1]) / 2
                        bb_upper = bb_upper_4h.iloc[-1]
                        
                        indicator_info.append(f"BB: `{round_value(bb_lower, 8)}` / `{round_value(bb_mid_val, 8)}` / `{round_value(bb_upper, 8)}`")
                        
                        # BB 동적 해석
                        if bb_position > 90:
                            indicator_info.append(f"  → 볼린저 밴드 상단을 크게 벗어났습니다. 과열 신호가 강합니다.\n")
                        elif bb_position > 80:
                            indicator_info.append(f"  → 상단 밴드에 근접했습니다. 상승 과열 구간이네요.\n")
                        elif bb_position > 60:
                            indicator_info.append(f"  → 중심선 위쪽에서 움직이고 있습니다. 상승 추세가 유지되고 있어요.\n")
                        elif bb_position > 40:
                            indicator_info.append(f"  → 중심선 근처입니다. 방향성을 찾고 있는 구간이에요.\n")
                        elif bb_position > 20:
                            indicator_info.append(f"  → 중심선 아래쪽입니다. 하락 압력이 있는 상황입니다.\n")
                        elif bb_position > 10:
                            indicator_info.append(f"  → 하단 밴드에 근접했습니다. 반등 가능성을 주시하세요.\n")
                        else:
                            indicator_info.append(f"  → 하단 밴드를 이탈했습니다. 강한 반등 시그널로 볼 수 있어요.\n")
                    
                    # MACD 동적 해석
                    if macd_val is not None and macdsignal_val is not None and macdhist_val is not None:
                        indicator_info.append(f"MACD: `{round_value(macd_val, 4)}` / Signal: `{round_value(macdsignal_val, 4)}` / Hist: `{round_value(macdhist_val, 4)}`")
                        
                        macd_diff = macd_val - macdsignal_val
                        if macd_val > macdsignal_val and macdhist_val > 0:
                            if abs(macd_diff) > 0.0005:
                                indicator_info.append(f"  → MACD가 시그널을 상회하며 상승 모멘텀이 강하게 확장되고 있습니다.\n")
                            else:
                                indicator_info.append(f"  → 골든크로스 초기 단계입니다. 상승 모멘텀이 형성 중이에요.\n")
                        elif macd_val > macdsignal_val:
                            indicator_info.append(f"  → MACD가 시그널 위에 있지만 모멘텀이 약화되고 있습니다. 주의가 필요해요.\n")
                        elif macd_val < macdsignal_val and macdhist_val < 0:
                            if abs(macd_diff) > 0.0005:
                                indicator_info.append(f"  → 데드크로스 후 하락 모멘텀이 가속화되고 있습니다.\n")
                            else:
                                indicator_info.append(f"  → 데드크로스 초기입니다. 하락 추세 진입 가능성이 높아요.\n")
                        else:
                            indicator_info.append(f"  → MACD가 시그널 아래 있지만 반등 조짐이 보입니다.\n")
                    
                    # ADX 동적 해석
                    if adx_val is not None:
                        indicator_info.append(f"ADX: `{adx_val:.2f}`")
                        
                        if adx_val >= 50:
                            indicator_info.append(f"  → 트렌드가 매우 강력합니다. 현재 방향성이 지속될 가능성이 높아요.\n")
                        elif adx_val >= 40:
                            indicator_info.append(f"  → 강한 추세가 진행 중입니다. 트렌드 추종 전략이 유효합니다.\n")
                        elif adx_val >= 25:
                            indicator_info.append(f"  → 추세가 형성되고 있습니다. 방향성이 뚜렷해지고 있어요.\n")
                        elif adx_val >= 20:
                            indicator_info.append(f"  → 약한 추세입니다. 아직 명확한 방향을 잡지 못했네요.\n")
                        else:
                            indicator_info.append(f"  → 추세가 없는 횡보장입니다. 구간 매매를 고려하세요.\n")
                    
                    # 추세 분석
                    if has_4h_data and trend_direction:
                        indicator_info.append("\n*추세 분석 (4H)*\n")
                        indicator_info.append(trend_direction)
                        if trend_slope is not None and current_rate > 0:
                            slope_pct = (trend_slope / current_rate) * 100
                            if abs(slope_pct) > 0.5:
                                strength = "강한" if abs(slope_pct) > 1.0 else "뚜렷한"
                                direction_txt = "상승" if slope_pct > 0 else "하락"
                                indicator_info.append(f"{strength} {direction_txt} 기울기를 보이고 있습니다. (`{slope_pct:+.4f}%`)")
                            else:
                                indicator_info.append(f"완만한 움직임입니다. (`{slope_pct:+.4f}%`)")
                    
                    # 종합 판단 (선택적)
                    if rsi_value is not None and macd_val is not None and macdsignal_val is not None:
                        indicator_info.append("\n*종합 판단*")
                        
                        bullish_signals = 0
                        bearish_signals = 0
                        
                        if rsi_value < 30:
                            bullish_signals += 1
                        elif rsi_value > 70:
                            bearish_signals += 1
                        
                        if macd_val > macdsignal_val and macdhist_val is not None and macdhist_val > 0:
                            bullish_signals += 1
                        elif macd_val < macdsignal_val and macdhist_val is not None and macdhist_val < 0:
                            bearish_signals += 1
                        
                        if bb_position is not None:
                            if bb_position < 20:
                                bullish_signals += 1
                            elif bb_position > 80:
                                bearish_signals += 1
                        
                        if bullish_signals > bearish_signals:
                            indicator_info.append("여러 지표가 상승 신호를 보이고 있습니다. 롱 포지션에 유리한 환경이에요. 🟢")
                        elif bearish_signals > bullish_signals:
                            indicator_info.append("하락 신호가 우세합니다. 숏 포지션이나 관망이 나을 수 있어요. 🔴")
                        else:
                            indicator_info.append("지표들이 엇갈린 신호를 보내고 있습니다. 신중한 접근이 필요합니다. ⚪")
                    
                    if indicator_info:
                        msg += "\n*주요 지표 (4H)*\n\n" + "\n".join(indicator_info) + "\n"

                    # 진입 신호 표시
                    if signal:
                        if signal == SignalDirection.LONG:
                            signal_emoji = "🟢"
                            signal_text = "LONG"
                        else:
                            signal_emoji = "🔴"
                            signal_text = "SHORT"
                        msg += f"*현재 진입 신호:* {signal_emoji} `{signal_text}`\n"
                        if enter_tag:
                            msg += f"*진입 태그:* `{enter_tag}`\n"
                        msg += "\n✅ *현재 진입 가능 상태입니다.*\n"
                    else:
                        msg += "\n*현재 진입 신호:* ⚪ `대기 중`\n"

                    # 시간 변환 (UTC -> KST)
                    kst = timezone(timedelta(hours=9))
                    if last_analyzed:
                        if last_analyzed.tzinfo is None:
                            last_analyzed = last_analyzed.replace(tzinfo=timezone.utc)
                        last_analyzed_kst = last_analyzed.astimezone(kst)
                        last_analyzed_str = last_analyzed_kst.strftime("%Y-%m-%d %H:%M:%S")
                    else:
                        last_analyzed_str = "-"

                    msg += f"\n*마지막 분석:* `{last_analyzed_str}` (KST)\n"

                    # 차트 링크 버튼 구성 (단일 버튼, 파라미터 제외)
                    chart_button = InlineKeyboardButton(text="차트", url=chart_base_url)
                    keyboard = InlineKeyboardMarkup([[chart_button]])
                    messages.append((msg, keyboard))

                except Exception as e:
                    logger.error(f"Error analyzing {pair}: {e}")
                    messages.append((f"📊 *{pair}*\n\n❌ 분석 중 오류 발생: {str(e)[:100]}\n", None))

            # 모든 메시지 전송
            if messages:
                for msg_text, keyboard in messages:
                    await update.message.reply_text(
                        msg_text,
                        parse_mode=ParseMode.MARKDOWN,
                        reply_markup=keyboard,
                    )
            else:
                await update.message.reply_text("📊 분석할 데이터가 없습니다.")

        except Exception as e:
            logger.error(f"Error in signal command: {e}")
            await update.message.reply_text(f"❌ 오류: {str(e)[:200]}")

    @authorized_only
    async def _custom_pnl(self, update: Update, context: CallbackContext) -> None:
        """
        PnL 카드 이미지를 전송하는 커스텀 명령어
        - 현재 포지션이 있으면 현재 포지션 이미지 전송
        - 없으면 가장 최근 종료된 포지션 이미지 전송
        """
        try:
            trade_info: dict[str, Any] | None = None
            is_closed = False

            # 1) 현재 열린 포지션이 있으면 우선 사용
            try:
                open_trades = self._rpc._rpc_trade_status()
                if open_trades and len(open_trades) > 0:
                    # open_date가 최신인 포지션 선택
                    latest_open = max(
                        open_trades,
                        key=lambda t: t.get("open_date") or t.get("open_timestamp", 0),
                    )
                    trade_info = latest_open
                    is_closed = False
                    logger.info(f"[PNL] 현재 포지션 사용: {trade_info.get('pair')} (#{trade_info.get('trade_id')})")
            except Exception as e:
                logger.debug(f"[PNL] 열린 포지션 조회 실패: {e}")
                trade_info = None

            # 2) 열린 포지션이 없으면 가장 최근 종료된 거래 사용
            if not trade_info:
                try:
                    history = self._rpc._rpc_trade_history(1)
                    if history and history.get("trades_count", 0) > 0 and history.get("trades"):
                        trade_info = history["trades"][0]
                        is_closed = True
                        logger.info(f"[PNL] 최근 종료 포지션 사용: {trade_info.get('pair')} (#{trade_info.get('trade_id')})")
                except Exception as e:
                    logger.debug(f"[PNL] 거래 이력 조회 실패: {e}")

            # 3) 포지션 정보가 없으면 메시지 전송
            if not trade_info:
                await update.message.reply_text("📊 최근 포지션(거래)이 없습니다.")
                return

            # 이미지 카드 전송
            chat_id = update.effective_chat.id if update and update.effective_chat else None
            await self._send_pnl_card_from_info(trade_info, is_closed, chat_id=chat_id)

        except Exception as e:
            from telegram.error import TimedOut, NetworkError
            import traceback
            logger.error(f"Error in custom pnl: {e}")
            logger.error(traceback.format_exc())
            # 타임아웃 오류는 이미지가 전송되었을 수 있으므로 사용자에게 오류 메시지를 표시하지 않음
            if isinstance(e, (TimedOut, NetworkError)):
                logger.warning(f"Telegram timeout in custom pnl (image may have been sent): {e}")
            else:
                await update.message.reply_text(f"❌ 오류: {str(e)[:200]}")

    async def send_pnl_card(self, trade):
        """
        포지션 종료 시 PnL 카드를 텔레그램으로 전송 (이미지 버전)
        """
        try:
            # 거래 정보 추출
            pair = trade.pair
            is_long = not trade.is_short
            entry_price = trade.open_rate
            
            # 열린 포지션의 경우 현재 가격으로 계산
            if trade.is_open:
                # 현재 가격 가져오기 (간단히 entry_price 사용, 실제로는 현재 시장가격을 가져와야 함)
                current_price = entry_price * (1.02 if is_long else 0.98)  # 임시로 2% 변동 가정
                exit_price = current_price
                profit_abs = trade.calc_profit(current_price)
                profit_pct = trade.calc_profit_ratio(current_price) * 100
            else:
                exit_price = trade.close_rate
                profit_abs = trade.calc_profit(trade.close_rate)
                profit_pct = trade.calc_profit_ratio(trade.close_rate) * 100
            
            amount = trade.amount
            
            # 거래 방향
            direction_text = "LONG" if is_long else "SHORT"
            
            # 수익/손실
            is_profit = profit_abs > 0
            pnl_text = "PROFIT" if is_profit else "LOSS"
            
            # 거래 시간 정보
            entry_time = trade.open_date.strftime("%m-%d %H:%M")
            exit_time = trade.close_date.strftime("%m-%d %H:%M")
            duration = trade.close_date - trade.open_date
            
            # 이미지 생성
            image_path = await self.create_pnl_image(
                pair=pair,
                direction=direction_text,
                entry_price=entry_price,
                exit_price=exit_price,
                amount=amount,
                profit_abs=profit_abs,
                profit_pct=profit_pct,
                is_profit=is_profit,
                entry_time=entry_time,
                exit_time=exit_time,
                duration=duration
            )
            
            # 이미지와 함께 텔레그램으로 전송 (사진만 전송, 캡션 없음)
            with open(image_path, 'rb') as photo:
                await self._app.bot.send_photo(
                    chat_id=self._config["telegram"]["chat_id"],
                    photo=photo,
                )
            
            # 임시 파일 삭제
            import os
            os.remove(image_path)
            
        except Exception as e:
            logger.error(f"Error sending PnL card: {e}")
            # 이미지 생성 실패 시 간단 알림만 전송
            await self._send_msg("이미지 생성에 실패했습니다.")

    async def create_pnl_image(self, pair, direction, entry_price, exit_price, amount, 
                              profit_abs, profit_pct, is_profit, entry_time, exit_time, duration,
                              quote_currency: str | None = None):
        """
        기존 3D 메탈릭 템플릿 이미지 위에 PnL 데이터 오버레이
        """
        try:
            import os
            import tempfile
            import math
            
            try:
                from PIL import Image, ImageDraw, ImageFont
            except Exception as e:
                raise RuntimeError(
                    "Pillow (PIL) is not installed. Please install dependencies: pip install -r requirements.txt"
                ) from e
            
            # 템플릿 이미지 경로 (사용자가 제공한 3D 메탈릭 이미지)
            user_data_dir = str(self._config.get("user_data_dir", "user_data"))
            template_path = os.path.join(user_data_dir, "pnl_template.png")
            
            # 템플릿 이미지가 없는 경우에도 동작하도록 기본 캔버스 생성
            if os.path.exists(template_path):
                img = Image.open(template_path)
            else:
                # 1200x630 소셜 카드 사이즈, 그라디언트 배경과 카드 영역 생성
                img = Image.new("RGB", (1200, 630), (18, 22, 34))
                grad = Image.new("RGB", img.size)
                for y in range(img.size[1]):
                    ratio = y / img.size[1]
                    r = int(18 + (32 - 18) * ratio)
                    g = int(22 + (36 - 22) * ratio)
                    b = int(34 + (54 - 34) * ratio)
                    for x in range(img.size[0]):
                        grad.putpixel((x, y), (r, g, b))
                img = Image.blend(img, grad, 0.6)
            draw = ImageDraw.Draw(img)
            
            # 이미지 크기 및 스케일
            width, height = img.size
            scale = max(width / 1200.0, height / 630.0)
            
            # 색상 설정
            text_primary = (255, 255, 255)
            text_secondary = (160, 160, 170)
            
            if is_profit:
                pnl_color = (34, 197, 94)  # 녹색
                accent_color = (34, 197, 94)
            else:
                pnl_color = (239, 68, 68)  # 빨간색
                accent_color = (239, 68, 68)
            
            # 폰트 설정 (고해상도용) - 이미지 크기에 맞춰 비율로 결정
            # 1순위: 사용자 제공 폰트(user_data/pnl_font.ttf, pnl_font_bold.ttf)
            # 2순위: 시스템 DejaVu
            # 3순위: 기본 폰트(가독성 낮음)
            user_data_dir = str(self._config.get("user_data_dir", "user_data"))
            user_font_regular = os.path.join(user_data_dir, "pnl_font.ttf")
            user_font_bold = os.path.join(user_data_dir, "pnl_font_bold.ttf")

            # 이미지 크기 기준 폰트 크기 계산
            # 전체적으로 약간 축소 (가독성 유지)
            title_size  = max(28, int(height * 0.050))
            large_size  = max(36, int(height * 0.080))
            normal_size = max(26, int(height * 0.045))
            small_size  = max(22, int(height * 0.035))

            def load_font(path: str, size: int):
                return ImageFont.truetype(path, size)

            try:
                if os.path.exists(user_font_regular) and os.path.exists(user_font_bold):
                    title_font  = load_font(user_font_bold, title_size)
                    large_font  = load_font(user_font_bold, large_size)
                    normal_font = load_font(user_font_regular, normal_size)
                    small_font  = load_font(user_font_regular, small_size)
                else:
                    title_font  = load_font("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", title_size)
                    large_font  = load_font("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", large_size)
                    normal_font = load_font("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", normal_size)
                    small_font  = load_font("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", small_size)
            except Exception:
                title_font = ImageFont.load_default()
                large_font = ImageFont.load_default()
                normal_font = ImageFont.load_default()
                small_font = ImageFont.load_default()
            
            # 텍스트 오버레이 - 미니멀, 중앙 정렬: Entry / Last / ROE
            def draw_centered(text: str, y: int, font, fill=(255,255,255)):
                bbox = draw.textbbox((0,0), text, font=font)
                tw = bbox[2]-bbox[0]
                draw.text((int((width - tw)/2), y), text, font=font, fill=fill)

            pnl_sign = "+" if is_profit else ""
            last_price = exit_price if exit_price else entry_price

            # 상단 여백
            # 상단 좌/우 헤더: 좌측에 심볼, 우측에 봇명
            pair_clean = pair.replace("/", "").replace(":USDT", "") or pair
            left_margin = int(width * 0.06)
            top_margin = int(height * 0.08)
            draw.text((left_margin, top_margin), pair_clean, font=title_font, fill=text_primary)

            bot_name = "KSH BTC"
            # 우상단 정렬
            bbox_bot = draw.textbbox((0, 0), bot_name, font=title_font)
            bot_w = bbox_bot[2] - bbox_bot[0]
            right_margin = int(width * 0.06)
            draw.text((width - right_margin - bot_w, top_margin), bot_name, font=title_font, fill=text_primary)

            # 방향/Perpetual 보조 라벨 (좌상단 심볼 아래, 작게)
            sub_label = f"{direction} · Perpetual"
            bbox_sub = draw.textbbox((0, 0), sub_label, font=small_font)
            sub_y = top_margin + (bbox_bot[3] - bbox_bot[1]) + int(height * 0.04)
            draw.text((left_margin, sub_y), sub_label, font=small_font, fill=text_secondary)

            # 본문 배치: 모두 좌측 정렬
            left_x = int(width * 0.08)
            # PnL 위치 고정, Entry/Last는 그 아래에 더 큰 간격으로 배치
            pnl_y_base = int(height * 0.56)
            line_gap = int(height * 0.08)

            # PnL 액수 (크게) + (ROE%) 괄호 표기 - 부호는 하나만 표시
            pct_abs = abs(profit_pct)
            abs_abs = abs(profit_abs)
            sign = "-" if profit_pct < 0 or profit_abs < 0 else ""
            cur = quote_currency or ""
            pnl_line = f"{sign}{abs_abs:.2f} {cur} ({sign}{pct_abs:.2f}%)".strip()
            # 큰 글씨 폭이 화면을 넘지 않도록 동적 축소
            max_width = int(width * 0.86) - left_x
            lf = large_font
            # 최대 5회 축소 시도
            for _ in range(5):
                bbox_tmp = draw.textbbox((left_x, 0), pnl_line, font=lf)
                if (bbox_tmp[2] - bbox_tmp[
                    0]) <= max_width:
                    break
                # 12%씩 축소
                try:
                    size = max(24, int(lf.size * 0.88))
                except AttributeError:
                    size = max(24, int(height * 0.07))
                try:
                    # 재로드 (사용자 폰트 우선)
                    if os.path.exists(user_font_bold):
                        lf = ImageFont.truetype(user_font_bold, size)
                    else:
                        lf = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", size)
                except Exception:
                    lf = large_font
                    break
            # PnL 위치 고정
            bbox_pnl = draw.textbbox((left_x, 0), pnl_line, font=lf)
            pnl_h = bbox_pnl[3] - bbox_pnl[1]
            pnl_y = pnl_y_base
            draw.text((left_x, pnl_y), pnl_line, font=lf, fill=accent_color)
            
            # Entry, Last - PnL 아래에 더 큰 간격으로 배치
            entry_start_y = pnl_y + pnl_h + int(height * 0.06)
            draw.text((left_x, entry_start_y), f"Entry  {entry_price:.6f}", font=normal_font, fill=text_primary)
            draw.text((left_x, entry_start_y + line_gap), f"Last   {last_price:.6f}", font=normal_font, fill=text_primary)
            
            # 임시 파일로 저장
            temp_file = tempfile.NamedTemporaryFile(delete=False, suffix='.png')
            img.save(temp_file.name, format='PNG', quality=100, optimize=True)
            temp_file.close()
            
            return temp_file.name
            
        except Exception as e:
            logger.error(f"Error creating PnL image overlay: {e}")
            raise e

    async def send_pnl_text_fallback(self, trade):
        """
        이미지 생성 실패 시 텍스트로 PnL 카드 전송
        """
        try:
            # 거래 정보 추출
            pair = trade.pair
            is_long = not trade.is_short
            entry_price = trade.open_rate
            exit_price = trade.close_rate
            amount = trade.amount
            profit_abs = trade.calc_profit(trade.close_rate)
            profit_pct = trade.calc_profit_ratio(trade.close_rate) * 100
            
            # 거래 방향 이모지
            direction_emoji = "🟢" if is_long else "🔴"
            direction_text = "LONG" if is_long else "SHORT"
            
            # 수익/손실 이모지
            if profit_abs > 0:
                pnl_emoji = "💰"
                pnl_text = "수익"
            else:
                pnl_emoji = "💸"
                pnl_text = "손실"
            
            # 거래 시간 정보
            entry_time = trade.open_date.strftime("%m-%d %H:%M")
            exit_time = trade.close_date.strftime("%m-%d %H:%M")
            duration = trade.close_date - trade.open_date
            
            # PnL 카드 메시지 구성
            card_message = f"""
{direction_emoji} **포지션 종료 알림** {pnl_emoji}

**거래 정보:**
• 페어: `{pair}`
• 방향: {direction_text} {direction_emoji}
• 수량: `{amount:.6f}`

**가격 정보:**
• 진입가: `{entry_price:.6f}`
• 청산가: `{exit_price:.6f}`

**수익/손실:**
• 금액: `{profit_abs:.2f} USDT` {pnl_emoji}
• 비율: `{profit_pct:.2f}%` {pnl_text}

**시간 정보:**
• 진입: {entry_time}
• 청산: {exit_time}
• 보유시간: {duration}

---
🤖 Freqtrade Bot
            """
            
            # 텔레그램으로 전송
            await self._app.bot.send_message(
                chat_id=self._config["telegram"]["chat_id"],
                text=card_message,
                parse_mode='Markdown'
            )
            
        except Exception as e:
            logger.error(f"Error sending PnL text fallback: {e}")


    async def _send_pnl_card_from_info(self, trade_info: dict[str, Any], is_closed: bool, chat_id: int | None = None) -> None:
        """
        RPC에서 가져온 dict 기반으로 이미지 PnL 카드 생성/전송
        """
        import os
        from datetime import datetime
        from telegram.error import TimedOut, NetworkError

        try:
            pair = trade_info.get("pair", "-")
            is_short = trade_info.get("is_short", False)
            direction = "LONG" if not is_short else "SHORT"

            open_rate = trade_info.get("open_rate") or trade_info.get("enter_rate") or 0.0
            amount = trade_info.get("amount", 0.0)

            if is_closed:
                exit_rate = trade_info.get("close_rate", open_rate)
                profit_abs = trade_info.get("close_profit_abs", 0.0) or 0.0
                profit_pct = (trade_info.get("close_profit", 0.0) or 0.0) * 100
                exit_time_str = self._format_dt_str(trade_info.get("close_timestamp") or trade_info.get("close_date"))
            else:
                exit_rate = trade_info.get("current_rate", open_rate)
                profit_abs = trade_info.get("profit_abs", 0.0) or 0.0
                profit_pct = (trade_info.get("profit_ratio", 0.0) or 0.0) * 100
                exit_time_str = datetime.utcnow().strftime("%m-%d %H:%M")

            entry_time_str = self._format_dt_str(trade_info.get("open_timestamp") or trade_info.get("open_date"))

            # 기간 계산 (표시용)
            duration = "-"
            try:
                od = self._to_datetime(trade_info.get("open_timestamp") or trade_info.get("open_date"))
                cd = self._to_datetime(trade_info.get("close_timestamp") or trade_info.get("close_date")) if is_closed else None
                if od and (cd or True):
                    end_dt = cd or datetime.utcnow()
                    duration = str(end_dt - od).split(".")[0]
            except Exception:
                pass

            image_path = await self.create_pnl_image(
                pair=pair,
                direction=direction,
                entry_price=float(open_rate or 0.0),
                exit_price=float(exit_rate or 0.0),
                amount=float(amount or 0.0),
                profit_abs=float(profit_abs or 0.0),
                profit_pct=float(profit_pct or 0.0),
                is_profit=float(profit_abs or 0.0) > 0.0,
                entry_time=entry_time_str,
                exit_time=exit_time_str,
                duration=duration,
                quote_currency=(trade_info.get("quote_currency") or self._config.get("stake_currency"))
            )

            # 타임아웃 예외 처리: 이미지 전송 시 타임아웃이 발생해도 이미지는 이미 생성되었으므로 무시
            try:
                with open(image_path, "rb") as photo:
                    await self._app.bot.send_photo(
                        chat_id=chat_id or self._config.get("telegram", {}).get("chat_id"),
                        photo=photo,
                        read_timeout=30,  # 타임아웃 시간 증가
                        write_timeout=30,
                        connect_timeout=30,
                    )
            except (TimedOut, NetworkError) as e:
                # 타임아웃이 발생해도 이미지는 이미 전송되었을 수 있으므로 로그만 남기고 계속 진행
                logger.warning(f"Telegram send_photo timeout (image may have been sent): {e}")
            finally:
                # 임시 파일 정리
                try:
                    if os.path.exists(image_path):
                        os.remove(image_path)
                except Exception:
                    pass
        except Exception as e:
            logger.error(f"Error in _send_pnl_card_from_info: {e}")
            import traceback
            logger.error(traceback.format_exc())
            # 이미지 생성 실패 시 사용자에게 알림
            chat_id = chat_id or self._config.get("telegram", {}).get("chat_id")
            if chat_id:
                try:
                    await self._app.bot.send_message(
                        chat_id=chat_id,
                        text=f"❌ PnL 이미지 생성 실패: {str(e)[:200]}"
                    )
                except Exception:
                    pass

    def _to_datetime(self, ts_or_dt: Any) -> datetime | None:
        if not ts_or_dt:
            return None
        if isinstance(ts_or_dt, datetime):
            return ts_or_dt
        try:
            # milliseconds or seconds
            ts = float(ts_or_dt)
            if ts > 1e12:
                ts = ts / 1000.0
            return datetime.fromtimestamp(ts)
        except Exception:
            return None

    def _format_dt_str(self, ts_or_dt: Any) -> str:
        dt = self._to_datetime(ts_or_dt)
        return dt.strftime("%m-%d %H:%M") if dt else "-"
