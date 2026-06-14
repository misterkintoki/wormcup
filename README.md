# WormCup Bot

Telegram miniapp automation for [WormCup](https://t.me/wormcupbot?startapp=EK3X2IX) — World Cup prediction game by Worm.

- **Tap-to-earn**: 100 plays/day per account
- **Score predictions**: spread strategy across multiple accounts
- **Daily check-in**: auto streak maintenance
- **Pure API**: no browser, no Telethon, no Playwright

## Quick Start

```bash
pip install -r requirements.txt
cp .env.example .env

# Paste your token(s) — one per line
echo "eyJhbG..." > token.txt

# Run
python wormcup.py
```

## Get Token

1. Open [WormCup](https://t.me/wormcupbot?startapp=EK3X2IX) in Telegram Desktop
2. F12 → Network → filter `api.worm.wtf`
3. Find `Authorization: Bearer eyJhbG...` header
4. Copy the token (without "Bearer ") → paste to `token.txt`

Token expires in **7 days**. Re-paste when expired.

## Multi-Account

```bash
# token.txt — one token per line
cat > token.txt << 'EOF'
eyJhbG...acc1
eyJhbG...acc2
eyJhbG...acc3
EOF
```

Each account gets **different score predictions** (spread strategy) to maximize pool coverage.

## Commands

```bash
python wormcup.py                    # run all: tap + predict
python wormcup.py --plan             # preview predictions only
python wormcup.py --status           # check token expiry
python wormcup.py --play 50          # 50 taps only
python wormcup.py --predict 0        # skip predictions
python wormcup.py --play 0 --predict 3  # predict only, no taps
python wormcup.py --loop 3600        # loop every hour
python wormcup.py --delay 20         # slower taps (anti-429)
```

## Config (.env)

| Variable | Default | Description |
|---|---|---|
| `WORMCUP_API` | `https://api.worm.wtf` | API endpoint |
| `WORMCUP_DELAY` | `15` | Seconds between taps |
| `WORMCUP_PREDICT` | `3` | Max matches to predict per account |
| `WORMCUP_TOKENS` | `./token.txt` | Token file path |
| `WORMCUP_SPREAD` | `true` | Different scores per account |
| `WORMCUP_PROXY` | *(empty)* | HTTP proxy URL |

CLI args override `.env` values.

## Spread Strategy

With multiple accounts, each gets a different score per match:

| Account | Match A | Match B |
|---|---|---|
| acc1 | 2-1 (primary) | 1-0 (primary) |
| acc2 | 1-0 (alt) | 2-1 (alt) |
| acc3 | 0-0 (contrarian) | 0-1 (counter-trend) |

- **Primary**: distribution-based (most likely)
- **Alt**: alternative realistic scores
- **Contrarian**: counter-trend picks (less competition)

Matches sorted by `predictor_count` ASC — less crowded pools first.

## 429 Rate Limit

Default delay is 15s between taps. If you get 429s:
- Increase `WORMCUP_DELAY=20` in `.env`
- Or `--delay 20` via CLI
- Script auto-retries on 429 (30-90s wait, max 3 retries)

## Proxy

If your VPS IP is blocked (HTTP 403), add a proxy to `.env`:

```
WORMCUP_PROXY=http://user:pass@host:port
```

Supports any HTTP proxy (DataImpulse, BrightData, SOAX, etc).
