<p align="center">
  <img src="https://raw.githubusercontent.com/freqtrade/freqtrade/develop/docs/assets/freqtrade_poweredby.svg" width="400" alt="Freqtrade" />
</p>

<h1 align="center">🤖 Freqtrade Custom Trading Bot</h1>

<p align="center">
  <b>Automated Crypto Futures Trading System with Advanced Trendline Detection & Korean Telegram Dashboard</b>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/exchange-Binance_Futures-F0B90B?style=flat-square&logo=binance" />
  <img src="https://img.shields.io/badge/framework-Freqtrade-blue?style=flat-square" />
  <img src="https://img.shields.io/badge/language-Python-3776AB?style=flat-square&logo=python&logoColor=white" />
  <img src="https://img.shields.io/badge/deploy-Docker-2496ED?style=flat-square&logo=docker&logoColor=white" />
  <img src="https://img.shields.io/badge/bot-Telegram-26A5E4?style=flat-square&logo=telegram&logoColor=white" />
</p>

---

## Overview

A customized [Freqtrade](https://github.com/freqtrade/freqtrade) deployment for **Binance USDT-M Futures** with:

- 📈 **Automated Trendline Detection** — scipy-based support/resistance line computation with linear regression
- 🎯 **4 Custom Strategies** — from BTC swing trading to low-cap altcoin OI-divergence plays
- 🇰🇷 **Full Korean Telegram Bot** — PnL cards, in-chat backtesting, live signal analysis
- 🐳 **One-command Docker deploy** — custom Dockerfile with Pillow for image generation

---

## Strategies

### 🔵 TrendPro (Primary)

> Scalp-optimized trendline strategy for BTC Futures

- Detects dynamic support/resistance using `scipy.signal.argrelextrema` + `numpy.polyfit`
- Multi-timeframe: 15m entries with 4h trend confirmation
- ATR-based dynamic stoploss (1.2× ATR, max -1.5%)
- Aggressive ROI: 0.3–1% targets in 0–30 min windows
- Shared trendline math via `user_data/lib/trendline_lib.py`

### 🟢 BtcImprovedTrendStrategy

> Swing-trade variant with wider stops for BTC trend-following

- Same trendline core, tuned for longer holds
- Stoploss -4%, ROI 4–10% targets
- 2× ATR dynamic stop with fast 3% cut on trend reversal
- Inline trendline computation (self-contained)

### 🟡 LowCapOIStrategy

> Counter-trend reversal strategy using Open Interest divergence

- Fetches **live Open Interest** from Binance Futures API via ccxt
- Detects OI/price divergence: price declining while OI increases → reversal signal
- Mark Price vs Last Price gap analysis (funding rate premium)
- Triple timeframe: 15m + 4h + 1d
- `confirm_trade_entry()` hook re-checks OI before execution
- Graceful backtesting fallback when live data unavailable

### ⚪ SimpleScalp

> Minimal demo strategy for testing & validation

- RSI + MACD + EMA crossover on 5m candles
- Long-only, micro-profit targets (0.05–0.3%)
- Intentionally loose conditions for trade generation testing

---

## Telegram Bot Features

All commands output in **Korean (한국어)** with inline keyboard navigation.

| Command | Description |
|---------|-------------|
| `/pnl` | 📸 Generates a **visual PnL card image** — styled trade result overlay with profit/loss, entry/exit prices, duration. Uses Pillow for image generation with custom fonts and auto-sizing. |
| `/backtesting` | 🧪 **Interactive backtesting** — multi-step conversation flow: strategy → start date → end date → runs backtest via subprocess → returns summary. Auto-deletes intermediate messages. |
| `/signal` | 📊 **Technical signal analysis** — computes RSI, Bollinger Bands, MACD, ADX on 4H data for all whitelisted pairs. Korean-language indicator interpretations with composite buy/sell signal. |
| `/position` | 💼 **Position dashboard** — all open trades with leverage, entry/current price, P&L %, stoploss. Timestamps in KST (UTC+9). Inline chart button → FreqUI. |
| `/mystatus` | 📋 Bot status with emoji formatting |
| `/mybalance` | 💰 Per-coin balance breakdown |
| `/mytrades` | 📈 Recent 5 trades with profit indicators |
| `/mystats` | 📉 Win rate, total trades, cumulative profit |

---

## Architecture

```
freqtrade/
├── freqtrade/
│   ├── rpc/
│   │   ├── telegram.py          # Custom commands: /pnl, /backtesting, /signal, /position
│   │   └── rpc_manager.py       # Korean state formatting
│   ├── freqtradebot.py          # Core bot (modified)
│   └── worker.py                # Worker process
├── user_data/
│   ├── strategies/
│   │   ├── TrendPro.py          # Primary scalp strategy
│   │   ├── BtcImprovedTrendStrategy.py
│   │   ├── LowCapOIStrategy.py  # OI-divergence strategy
│   │   └── SimpleScalp.py       # Demo strategy
│   ├── lib/
│   │   └── trendline_lib.py     # Shared trendline math library
│   ├── config.json              # Bot configuration
│   └── telegram_patch.py        # Additional custom commands
├── docker/
│   └── Dockerfile.custom        # Adds Pillow for PnL cards
└── docker-compose.yml           # One-command deployment
```

---

## Quick Start

### 1. Clone & Configure

```bash
git clone https://github.com/SangHyeonKwon/freqtrade.git
cd freqtrade
```

Edit `user_data/config.json`:
```json
{
  "exchange": {
    "key": "YOUR_BINANCE_API_KEY",
    "secret": "YOUR_BINANCE_API_SECRET"
  },
  "telegram": {
    "token": "YOUR_TELEGRAM_BOT_TOKEN",
    "chat_id": "YOUR_CHAT_ID"
  }
}
```

### 2. Deploy with Docker

```bash
docker compose up -d
```

The bot starts trading with the **TrendPro** strategy on BTC/USDT Futures.

### 3. Access

- **Telegram**: Open your bot → `/signal` for market analysis, `/position` for trades
- **FreqUI**: `http://localhost:8081` (user: `freqtrader`)

---

## Trendline Detection Engine

The core differentiator — automated support/resistance trendline computation:

```
1. Fetch OHLCV data (15m candles)
2. Find local extrema via scipy.signal.argrelextrema(order=5)
3. Fit linear regression through extrema points (numpy.polyfit)
4. Apply weighted regression — recent data weighted 4× more
5. Smooth with rolling window to reduce noise
6. Fallback to EMA when insufficient data points
```

Entry signals trigger when price interacts with trendlines + momentum confirmation (RSI, MACD, ADX, volume spike).

---

## Configuration

| Parameter | Default | Description |
|-----------|---------|-------------|
| `strategy` | TrendPro | Active strategy |
| `max_open_trades` | 3 | Maximum concurrent positions |
| `trading_mode` | futures | Binance USDT-M Futures |
| `margin_mode` | isolated | Per-position margin |
| `dry_run` | true | Paper trading mode |
| `dry_run_wallet` | 1000 | Simulated USDT balance |
| `stake_amount` | unlimited | Uses 99% of available balance |

---

## Tech Stack

- **Framework**: [Freqtrade](https://github.com/freqtrade/freqtrade) (open-source trading bot)
- **Exchange**: Binance Futures via [ccxt](https://github.com/ccxt/ccxt)
- **Indicators**: [TA-Lib](https://ta-lib.org/) + scipy + numpy
- **Telegram**: python-telegram-bot with custom command handlers
- **Image Gen**: Pillow (PnL card rendering)
- **Deploy**: Docker Compose with source-mounted volumes

---

## Disclaimer

> ⚠️ This software is for **educational purposes only**. Cryptocurrency futures trading involves significant risk of loss. Past performance does not guarantee future results. Use at your own risk.

---

<p align="center">
  Built with ❤️ on top of <a href="https://github.com/freqtrade/freqtrade">Freqtrade</a>
</p>
