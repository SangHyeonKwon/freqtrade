#!/usr/bin/env python3
"""Fix the _send_pnl_card_from_info method indentation"""

# Read the file
with open('freqtrade/rpc/telegram.py', 'r', encoding='utf-8') as f:
    content = f.read()

# Find the method and replace it with correctly indented version
old_method = """    async def _send_pnl_card_from_info(self, trade_info: dict[str, Any], is_closed: bool, chat_id: int | None = None) -> None:
        \"\"\"
        RPC에서 가져온 dict 기반으로 이미지 PnL 카드 생성/전송
        \"\"\"
        import os
        from datetime import datetime
        from telegram.error import TimedOut, NetworkError

        try:"""

new_method = """    async def _send_pnl_card_from_info(self, trade_info: dict[str, Any], is_closed: bool, chat_id: int | None = None) -> None:
        \"\"\"
        RPC에서 가져온 dict 기반으로 이미지 PnL 카드 생성/전송
        \"\"\"
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
            # 이미지 생성 실패 시 에러 메시지 전송하지 않음 (이미 타임아웃 오류가 표시되었을 수 있음)"""

# Find the start and end of the method
import re
pattern = r'    async def _send_pnl_card_from_info\(self, trade_info: dict\[str, Any\], is_closed: bool, chat_id: int \| None = None\) -> None:.*?except Exception as e:.*?logger\.error\(f"Error in _send_pnl_card_from_info: {e}"\)'
match = re.search(pattern, content, re.DOTALL)

if match:
    # Replace the method
    start = match.start()
    # Find the end of the method (before _to_datetime)
    end_pattern = r'    def _to_datetime\(self'
    end_match = re.search(end_pattern, content)
    if end_match:
        end = end_match.start()
        # Replace the method
        new_content = content[:start] + new_method + "\n\n" + content[end:]
        with open('freqtrade/rpc/telegram.py', 'w', encoding='utf-8') as f:
            f.write(new_content)
        print("Fixed _send_pnl_card_from_info method")
    else:
        print("Could not find end of method")
else:
    print("Could not find method to replace")

