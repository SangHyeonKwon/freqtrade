# Freqtrade 커스텀 텔레그램 명령어 추가 가이드

이 가이드는 freqtrade 텔레그램 봇에 커스텀 명령어를 추가하는 방법을 설명합니다.

## 🎯 추가된 커스텀 명령어

다음 커스텀 명령어들이 추가되었습니다:

- `/mystatus` - 커스텀 봇 상태 확인 (한국어 + 이모지)
- `/mybalance` - 커스텀 잔고 정보 (상세한 코인별 잔고)
- `/mytrades` - 커스텀 거래 내역 (최근 5개 거래 상세 정보)
- `/mystats` - 커스텀 통계 정보 (상세한 거래 통계)
- `/backtesting` - 백테스팅 실행 (전략명, 시작일, 종료일 입력)
- `/myhelp` - 커스텀 도움말 (한국어 도움말)

## 🔧 설치 방법

### 1. 파일 수정 완료
다음 파일이 이미 수정되었습니다:
- `freqtrade/rpc/telegram.py` - 커스텀 명령어 핸들러 추가

### 2. freqtrade 봇 재시작

#### Docker 환경에서:
```bash
# Docker 컨테이너 재시작
docker-compose down
docker-compose up -d

# 또는 컨테이너만 재시작
docker-compose restart freqtrade
```

#### 일반 환경에서:
```bash
# freqtrade 봇을 중지하고 다시 시작
freqtrade trade --config user_data/config.json
```

### 3. 텔레그램에서 테스트
텔레그램 봇과 연결된 채팅에서 다음 명령어들을 테스트해보세요:

```
/mystatus
/mybalance
/mytrades
/mystats
/backtesting BtcTrendStrategy 2024-01-01 2024-12-31
/myhelp
```

### 4. 백테스팅 명령어 사용 예시
```
# BtcTrendStrategy로 2024년 1월 백테스팅
/backtesting BtcTrendStrategy 2024-01-01 2024-01-31

# Trend_Follow_BTC로 2024년 6월 백테스팅
/backtesting Trend_Follow_BTC 2024-06-01 2024-06-30

# BtcCounterTrendStrategy로 특정 기간 백테스팅
/backtesting BtcCounterTrendStrategy 2024-03-01 2024-03-31

# 도움말 보기
/backtesting
```

### 5. Docker 환경에서 백테스팅 테스트
```bash
# Docker 컨테이너 상태 확인
docker-compose ps

# 백테스팅 테스트 스크립트 실행
python user_data/test_backtesting.py

# 수동으로 백테스팅 실행
docker-compose exec freqtrade freqtrade backtesting --strategy BtcTrendStrategy --timerange 20240101-20240110 --config user_data/config.json
```

## 📋 커스텀 명령어 상세 설명

### `/mystatus` - 커스텀 상태
- 기본 `/status` 명령어의 한국어 버전
- 이모지와 함께 더 보기 좋은 형태로 표시
- 봇 상태, 수익률, 활성 거래 수 등을 표시

### `/mybalance` - 커스텀 잔고
- 기본 `/balance` 명령어의 상세 버전
- 각 코인별 잔고를 개별적으로 표시
- 0.00000001 BTC 미만의 잔고는 제외하여 깔끔하게 표시

### `/mytrades` - 커스텀 거래 내역
- 기본 `/trades` 명령어의 상세 버전
- 최근 5개 거래만 표시하여 가독성 향상
- 각 거래의 수익률, 수량, 진입가, 손절가 등을 상세히 표시

### `/mystats` - 커스텀 통계
- 기본 `/stats` 명령어의 상세 버전
- 총 거래 수, 승률, 수익 정보 등을 한국어로 표시
- 거래 기간 정보도 포함

### `/backtesting` - 백테스팅 실행
- 전략명, 시작일, 종료일을 입력받아 백테스팅 실행
- BTC/USDT:USDT 페어로 자동 백테스팅
- 사용법: `/backtesting <전략명> <시작일> <종료일>`
- 예시: `/backtesting BtcTrendStrategy 2024-01-01 2024-12-31`
- 결과는 JSON 파일로 저장되고 텔레그램으로 요약 제공

### `/myhelp` - 커스텀 도움말
- 기본 `/help` 명령어의 한국어 버전
- 커스텀 명령어와 기본 명령어를 모두 설명
- 사용법과 차이점을 명확히 설명

## 🛠️ 추가 커스텀 명령어 만들기

새로운 커스텀 명령어를 추가하려면:

### 1. 핸들러 등록
`freqtrade/rpc/telegram.py`의 `handles` 리스트에 새 명령어 추가:
```python
CommandHandler("mynewcommand", self._custom_new_command),
```

### 2. 메서드 구현
클래스 끝에 새 메서드 추가:
```python
@authorized_only
async def _custom_new_command(self, update: Update, context: CallbackContext) -> None:
    """
    Custom new command: /mynewcommand
    """
    try:
        # 여기에 커맨드 로직 구현
        message = "새로운 커스텀 명령어입니다!"
        await update.message.reply_text(message, parse_mode='Markdown')
        
    except Exception as e:
        logger.error(f"Error in custom new command: {e}")
        await update.message.reply_text("❌ 오류가 발생했습니다.")
```

### 3. 봇 재시작
변경사항을 적용하려면 freqtrade 봇을 재시작해야 합니다.

## 🔍 문제 해결

### 명령어가 작동하지 않는 경우
1. freqtrade 봇이 재시작되었는지 확인
2. 텔레그램 설정이 올바른지 확인 (`config.json`의 `telegram` 섹션)
3. 로그 파일에서 오류 메시지 확인

### 백테스팅 명령어 관련 문제
1. **전략 파일이 존재하는지 확인**: `user_data/strategies/` 폴더에 전략 파일이 있는지 확인
2. **날짜 형식 확인**: YYYY-MM-DD 형식으로 입력했는지 확인
3. **데이터 존재 여부**: 해당 기간의 데이터가 다운로드되어 있는지 확인
4. **권한 문제**: 백테스팅 결과 파일을 저장할 권한이 있는지 확인

### 권한 오류가 발생하는 경우
1. `config.json`의 `telegram.authorized_users` 설정 확인
2. 텔레그램 채팅 ID가 올바른지 확인

### 메시지가 표시되지 않는 경우
1. 텔레그램 봇 토큰이 유효한지 확인
2. 봇이 채팅에 추가되어 있는지 확인
3. 네트워크 연결 상태 확인

### 백테스팅이 실패하는 경우
1. **전략 파일 오류**: 전략 파일에 문법 오류가 있는지 확인
2. **데이터 부족**: 해당 기간의 데이터가 충분한지 확인
3. **메모리 부족**: 백테스팅 기간이 너무 길어서 메모리 부족이 발생할 수 있음
4. **디스크 공간**: 백테스팅 결과 파일을 저장할 디스크 공간이 충분한지 확인
5. **Docker 컨테이너 상태**: Docker 컨테이너가 실행 중인지 확인
6. **Docker 볼륨 마운트**: user_data 폴더가 올바르게 마운트되었는지 확인

## 📝 주의사항

- 이 수정사항은 freqtrade의 핵심 파일을 변경하므로, freqtrade 업데이트 시 덮어써질 수 있습니다.
- 업데이트 후에는 다시 커스텀 명령어를 추가해야 할 수 있습니다.
- 백업을 만들어두는 것을 권장합니다.

## 🚀 백테스팅 명령어 특징

### ✅ 자동화된 기능
- **페어 자동 설정**: BTC/USDT:USDT로 자동 백테스팅
- **결과 자동 저장**: JSON 파일로 자동 저장
- **진행상황 알림**: 시작/완료/실패 상태를 텔레그램으로 알림
- **결과 요약**: 주요 통계를 텔레그램으로 요약 제공
- **Docker 지원**: Docker 환경에서 자동으로 백테스팅 실행
- **날짜 형식 자동 변환**: YYYY-MM-DD 형식을 freqtrade 형식으로 자동 변환

### 📊 지원하는 전략
- `BtcTrendStrategy` - BTC 트렌드 전략
- `BtcCounterTrendStrategy` - BTC 카운터 트렌드 전략  
- `Trend_Follow_BTC` - BTC 트렌드 팔로우 전략

### 📁 결과 파일 위치
백테스팅 결과는 다음 위치에 저장됩니다:
```
user_data/backtest_results/backtest_{전략명}_{시작일}_{종료일}.json
```

예시: `backtest_BtcTrendStrategy_2024-01-01_2024-12-31.json`

## 🚀 확장 가능성

이 기본 구조를 바탕으로 다음과 같은 기능들을 추가할 수 있습니다:

- 특정 코인에 대한 상세 정보 조회
- 거래 히스토리 검색 및 필터링
- 알림 설정 및 관리
- 봇 성능 모니터링
- 커스텀 차트 생성
- 데이터 내보내기 기능
- 백테스팅 결과 비교 기능
- 전략 성능 순위 기능

## 📞 지원

문제가 발생하거나 추가 기능이 필요한 경우, freqtrade 커뮤니티나 GitHub 이슈를 통해 도움을 요청할 수 있습니다.
