# WormCup API Runner

Pure API automation untuk Telegram miniapp WormCup.

- No Playwright / no browser automation.
- Telethon hanya untuk login Telegram dan refresh `tgWebAppData`.
- API requests pakai `curl_cffi` supaya request fingerprint mirip Chrome dan tidak mentok Cloudflare.
- Multi-account: simpan session per akun di `sessions/<name>.session`.
- Prediksi tidak random: skor dipilih dari data `distribution` match API WormCup.

## Install

```bash
python -m pip install -r requirements.txt
```

## Run

```bash
python p.py
```

Menu:

1. Tambah/refresh account
2. Existing accounts: clear daily/play/predict
3. Existing one account
4. Show accounts
0. Exit

Menu 2/3 sekarang bisa loop:

- `Play count -1` = tap semua remaining
- `Play count 0` = skip play
- `Play count N` = tap N kali saja
- `Loop interval 0` = sekali jalan
- `Loop interval 300` = ulang tiap 5 menit dengan countdown animasi

## CLI non-interaktif

List akun:

```bash
python p.py --list
```

Run semua akun, claim daily, tap 10x per akun, predict 3 match open:

```bash
python p.py --run-all --play-count 10 --predict-max 3 --delay 15
```

Run semua akun, tap semua remaining dengan delay anti-429:

```bash
python p.py --run-all --play-count -1 --predict-max 3 --delay 15
```

Run satu akun:

```bash
python p.py --run main --play-count 5 --predict-max 1 --delay 15
```

Run loop tiap 5 menit:

```bash
python p.py --run-all --play-count -1 --predict-max 3 --delay 15 --loop 300
```

Skip play atau skip predict:

```bash
python p.py --run-all --play-count 0 --predict-max 0
```

## Multi-account 5 akun

Login satu-satu dari menu 1, beri nama misalnya:

- main
- acc2
- acc3
- acc4
- acc5

Nanti `accounts.json` otomatis berisi 5 akun, session tersimpan di folder `sessions/`.

## Tentang 429 saat play

HTTP 429 = rate limit dari server karena tap terlalu cepat. Script sekarang handle dengan:

- default delay antar tap 15 detik
- kalau kena 429, wait 30-90 detik lalu retry max 3x
- `--delay` bisa dinaikkan kalau server masih galak, contoh `--delay 20`

## Prediksi

Exact-score prediction tidak bisa dijamin. Script tidak ngarang/random; dia ambil `distribution` dari endpoint `/api/worldcup/matches/` lalu pilih skor deterministik:

- draw kuat/close market -> 0-0 atau 1-1
- home favorit berat -> 2-0
- home favorit sedang -> 2-1
- home slight edge -> 1-0
- away favorit berat -> 0-2
- away favorit sedang -> 1-2
- away slight edge -> 0-1

Alasan prediksi dicetak di output, contoh:

```text
Germany vs Curacao: 2-0 -> 200 | Germany heavy favorite: home=65% away=12%
```

## GitHub safety

Jangan upload session/auth:

- `sessions/`
- `data/`
- `accounts.json`

Semua sudah masuk `.gitignore`.
