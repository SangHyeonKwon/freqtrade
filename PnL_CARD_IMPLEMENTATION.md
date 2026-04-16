# PnL 카드 이미지 생성 및 백테스팅 자동 삭제 구현 가이드

## 1. 이미지 위에 텍스트 오버레이하여 전송하기

### 개요
Telegram 봇에서 `/pnl` 명령어를 입력하면, 템플릿 이미지 위에 거래 정보를 텍스트로 오버레이한 이미지를 생성하여 전송합니다.

### 구현 과정

#### 1.1 라이브러리 준비
- **Pillow (PIL)**: 이미지 처리를 위한 라이브러리
  - `pip install Pillow==10.4.0` 또는 `requirements.txt`에 추가
  - `Image`: 이미지 열기/생성
  - `ImageDraw`: 텍스트 그리기
  - `ImageFont`: 폰트 로드

#### 1.2 템플릿 이미지 준비
- 템플릿 이미지를 `user_data/pnl_template.png` 경로에 저장
- 템플릿이 없으면 그라디언트 배경을 자동 생성

#### 1.3 폰트 준비 (선택사항, 권장)
- `user_data/pnl_font.ttf`: 일반 텍스트용
- `user_data/pnl_font_bold.ttf`: 굵은 텍스트용
- 폰트가 없으면 시스템 기본 폰트 사용 (가독성 낮음)

#### 1.4 이미지 생성 프로세스

```python
# 1. 템플릿 이미지 로드 또는 생성
if os.path.exists(template_path):
    img = Image.open(template_path)  # 사용자 템플릿 사용
else:
    img = Image.new("RGB", (1200, 630), (18, 22, 34))  # 기본 배경 생성

# 2. ImageDraw 객체 생성 (텍스트 그리기용)
draw = ImageDraw.Draw(img)

# 3. 폰트 로드 (이미지 크기에 비례하여 크기 결정)
title_size = int(height * 0.050)  # 이미지 높이의 5%
large_size = int(height * 0.080)  # 이미지 높이의 8%
normal_size = int(height * 0.045)  # 이미지 높이의 4.5%

# 사용자 폰트 우선, 없으면 시스템 폰트
if os.path.exists(user_font_bold):
    large_font = ImageFont.truetype(user_font_bold, large_size)
else:
    large_font = ImageFont.truetype("/usr/share/fonts/.../DejaVuSans-Bold.ttf", large_size)

# 4. 텍스트 그리기
# 좌상단: 페어명
draw.text((left_margin, top_margin), "BTCUSDT", font=title_font, fill=(255,255,255))

# 우상단: 봇명
draw.text((width - right_margin - bot_w, top_margin), "KSH BTC", font=title_font, fill=(255,255,255))

# 본문: PnL 정보 (크게, 손익에 따라 색상 변경)
pnl_color = (34, 197, 94) if is_profit else (239, 68, 68)  # 녹색 또는 빨강
draw.text((left_x, pnl_y), f"-38.28 USDT (-15.71%)", font=large_font, fill=pnl_color)

# Entry, Last 가격
draw.text((left_x, entry_y), f"Entry  121833", font=normal_font, fill=(255,255,255))
draw.text((left_x, entry_y + gap), f"Last  103157", font=normal_font, fill=(255,255,255))

# 5. 임시 파일로 저장
temp_file = tempfile.NamedTemporaryFile(delete=False, suffix='.png')
img.save(temp_file.name, format='PNG', quality=100)
```

#### 1.5 텔레그램으로 이미지 전송

```python
# 생성된 이미지 파일을 텔레그램으로 전송
with open(image_path, "rb") as photo:
    await self._app.bot.send_photo(
        chat_id=chat_id,
        photo=photo,  # 이미지만 전송 (캡션 없음)
    )

# 임시 파일 삭제
os.remove(image_path)
```

### 핵심 포인트

1. **이미지 크기 비례 폰트**: 템플릿 이미지 크기에 맞춰 폰트 크기를 비율로 계산
2. **동적 폰트 축소**: 텍스트가 화면을 넘어가면 자동으로 폰트 크기 축소 (최대 5회, 12%씩)
3. **위치 계산**: 이미지 크기의 비율로 위치 계산 (예: `int(height * 0.56)`)
4. **색상 관리**: 손익에 따라 색상 자동 변경 (수익: 녹색, 손실: 빨강)
5. **텍스트 정렬**: `textbbox()`로 텍스트 너비 계산 후 중앙/오른쪽 정렬

---

## 2. 백테스팅 명령어 자동 메시지 삭제

### 개요
`/backtesting` 명령어 실행 시, 사용자가 입력한 명령 메시지와 "백테스팅 시작" 안내 메시지를 결과 전송 후 자동으로 삭제하여 채팅창을 깔끔하게 유지합니다.

### 구현 과정

#### 2.1 메시지 ID 저장

```python
async def _custom_backtesting(self, update, context):
    # 1. 사용자 입력 명령 메시지 ID 저장
    command_chat_id = update.effective_chat.id
    command_message_id = update.message.message_id
    
    # 2. "백테스팅 시작" 안내 메시지 전송 및 ID 저장
    start_msg = await update.message.reply_text(
        f"🚀 백테스팅 시작: {strategy_name} ({start_date} ~ {end_date})"
    )
    # start_msg.message_id에 메시지 ID가 저장됨
```

#### 2.2 백테스팅 실행

```python
# subprocess로 백테스팅 실행
cmd = ["freqtrade", "backtesting", "--strategy", strategy_name, ...]
result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, ...)

# 결과 파싱 및 전송
if result.returncode == 0:
    # 결과 요약 생성
    summary_text = "\n".join(summary[:8])
    await update.message.reply_text(f"✅ 백테스팅 완료\n\n📊 결과 요약\n{summary_text}")
```

#### 2.3 결과 전송 후 메시지 삭제

```python
# 백테스팅 완료 후 (성공/실패 무관)

# 1. 사용자 입력 명령 메시지 삭제
if command_chat_id and command_message_id:
    try:
        await context.bot.delete_message(
            chat_id=command_chat_id,
            message_id=command_message_id
        )
    except Exception:
        pass  # 삭제 실패 시 무시 (권한 없음 등)

# 2. "백테스팅 시작" 안내 메시지 삭제
if start_msg:
    try:
        await context.bot.delete_message(
            chat_id=start_msg.chat.id,
            message_id=start_msg.message_id
        )
    except Exception:
        pass
```

### 핵심 포인트

1. **메시지 ID 저장**: `reply_text()` 반환값에 메시지 정보 포함
2. **결과 후 삭제**: 백테스팅 완료 후 결과 전송 직후 삭제 실행
3. **에러 처리**: 삭제 실패 시 무시 (권한 없음, 메시지 없음 등)
4. **최종 결과만 유지**: 채팅창에는 최종 결과 메시지만 남음

### 동작 흐름

```
사용자 입력: /backtesting Strategy 2024-01-01 2024-06-30
    ↓
봇: "🚀 백테스팅 시작: Strategy (2024-01-01 ~ 2024-06-30)" (삭제 대상)
    ↓
백테스팅 실행 (시간 소요)
    ↓
봇: "✅ 백테스팅 완료\n\n📊 결과 요약\n..." (최종 결과, 유지)
    ↓
자동 삭제:
  - 사용자 입력 메시지 삭제
  - "백테스팅 시작" 메시지 삭제
```

---

## 요약

### 이미지 텍스트 오버레이
- **Pillow 라이브러리**로 이미지에 텍스트 그리기
- **이미지 크기 비례** 폰트 크기 계산
- **동적 폰트 축소**로 텍스트 잘림 방지
- **임시 파일**로 이미지 저장 후 전송하고 삭제

### 백테스팅 자동 삭제
- **메시지 ID 저장**으로 삭제 대상 추적
- **결과 전송 후** 자동 삭제 실행
- **에러 처리**로 안정성 확보
- **최종 결과만 유지**하여 채팅창 정리












