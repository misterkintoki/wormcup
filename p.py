#!/usr/bin/env python3
"""
WormCup Telegram miniapp runner.

Pure API requests + Telethon for WebView initData refresh. No Playwright/browser.
Prediction strategy is deterministic from WormCup API match distribution, not random.
"""
import argparse
import asyncio
import json
import os
import random
import re
import sys
import time
from urllib.parse import unquote

from curl_cffi import requests
from telethon import TelegramClient, functions

API_ID = int(os.getenv('TG_API_ID', '2496'))
API_HASH = os.getenv('TG_API_HASH', '8da85b0d5bfe62527e5b244c209159c3')
BOT = os.getenv('WORMCUP_BOT', 'wormcupbot')
DEFAULT_START = os.getenv('WORMCUP_START_PARAM', 'CLSQGLT')
DEFAULT_PLAY_DELAY = float(os.getenv('WORMCUP_PLAY_DELAY', '15'))
DEFAULT_PLAY_LIMIT = int(os.getenv('WORMCUP_PLAY_LIMIT', '-1'))  # -1 = all remaining, 0 = skip
DEFAULT_PREDICT_MAX = int(os.getenv('WORMCUP_PREDICT_MAX', '3'))
DEFAULT_LOOP_INTERVAL = int(os.getenv('WORMCUP_LOOP_INTERVAL', '300'))
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SESS_DIR = os.path.join(BASE_DIR, 'sessions')
DATA_DIR = os.path.join(BASE_DIR, 'data')
ACCOUNTS_FILE = os.path.join(BASE_DIR, 'accounts.json')
WC = 'https://wc.worm.wtf'
WORM = 'https://api.worm.wtf'

os.makedirs(SESS_DIR, exist_ok=True)
os.makedirs(DATA_DIR, exist_ok=True)


def mask_phone(p):
    p = (p or '').strip()
    return p[:4] + '****' + p[-4:] if len(p) >= 8 else p


def load_accounts():
    if not os.path.exists(ACCOUNTS_FILE):
        return []
    with open(ACCOUNTS_FILE) as f:
        return json.load(f)


def save_accounts(accs):
    with open(ACCOUNTS_FILE, 'w') as f:
        json.dump(accs, f, indent=2)


def extract_init(url):
    m = re.search(r'tgWebAppData=([^&]+)', url or '')
    return unquote(m.group(1)) if m else None


def short(obj, n=180):
    s = obj if isinstance(obj, str) else json.dumps(obj, ensure_ascii=False)
    return s.replace('\n', ' ')[:n]


async def get_webview(account, interactive_login=False):
    name = account['name']
    phone = account.get('phone', '')
    start = account.get('start_param') or DEFAULT_START
    sess_path = os.path.join(BASE_DIR, account.get('session') or f'sessions/{name}.session')
    client = TelegramClient(sess_path, API_ID, API_HASH)
    await client.connect()
    if not await client.is_user_authorized():
        if not interactive_login:
            raise RuntimeError(f'{name}: session belum login, pilih menu 1 dulu')
        real_phone = phone if '*' not in phone else input('Phone lengkap (+62...): ').strip()
        await client.send_code_request(real_phone)
        code = input('Code Telegram: ').strip()
        try:
            await client.sign_in(real_phone, code)
        except Exception as e:
            if 'SESSION_PASSWORD_NEEDED' in type(e).__name__ or 'password' in str(e).lower():
                pw = input('2FA password: ')
                await client.sign_in(password=pw)
            else:
                raise
        account['phone'] = mask_phone(real_phone)
    me = await client.get_me()
    bot = await client.get_entity(BOT)
    try:
        await client.send_message(bot, f'/start {start}')
    except Exception:
        pass

    urls = []
    try:
        r = await client(functions.messages.RequestMainWebViewRequest(
            peer=bot, bot=bot, platform='android', start_param=start, fullscreen=True
        ))
        urls.append({'kind': 'main', 'url': r.url, 'initData': extract_init(r.url)})
    except Exception as e:
        urls.append({'kind': 'main_error', 'error': repr(e)})
    try:
        full = await client(functions.users.GetFullUserRequest(bot))
        menu_url = getattr(getattr(full.full_user.bot_info, 'menu_button', None), 'url', None)
        if menu_url:
            r = await client(functions.messages.RequestWebViewRequest(
                peer=bot, bot=bot, platform='android', url=menu_url,
                start_param=start, from_bot_menu=True
            ))
            urls.append({'kind': 'menu', 'url': r.url, 'initData': extract_init(r.url)})
    except Exception as e:
        urls.append({'kind': 'menu_error', 'error': repr(e)})
    await client.disconnect()

    with open(os.path.join(DATA_DIR, f'{name}_webview.json'), 'w') as f:
        json.dump(urls, f, indent=2)
    init = next((x.get('initData') for x in urls if x.get('initData') and x.get('kind') == 'main'), None)
    init = init or next((x.get('initData') for x in urls if x.get('initData')), None)
    if not init:
        raise RuntimeError(f'{name}: gagal ambil initData')
    print(f'LOGIN/WEBVIEW OK {name} @{getattr(me, "username", None)} id={me.id}')
    return init


def load_saved_init(name):
    path = os.path.join(DATA_DIR, f'{name}_webview.json')
    if not os.path.exists(path) and name == 'main':
        path = os.path.join(DATA_DIR, 'main_webview.json')
    if not os.path.exists(path):
        return None
    arr = json.load(open(path))
    return next((x.get('initData') for x in arr if x.get('initData') and x.get('kind') == 'main'), None) or next((x.get('initData') for x in arr if x.get('initData')), None)


def pick_score(match):
    """Deterministic prediction from API distribution.

    Exact-score markets are impossible to guarantee. This does NOT invent external
    sports knowledge; it uses only WormCup API fields: distribution percentages,
    pool status, teams, kickoff/status. Returns (home_score, away_score, reason).
    """
    dist = match.get('distribution') or {}
    hp = int(dist.get('home_pct') or 0)
    dp = int(dist.get('draw_pct') or 0)
    ap = int(dist.get('away_pct') or 0)
    home = match.get('home', {}).get('name', 'Home')
    away = match.get('away', {}).get('name', 'Away')
    top = max(hp, dp, ap)

    # If market is close or draw has serious share, choose low-scoring draw.
    if dp >= 28 and (top - dp) <= 8:
        return 1, 1, f'draw value: draw={dp}% close to top={top}%'
    if dp >= 34:
        return 0, 0, f'strong draw distribution: draw={dp}%'

    if hp >= ap:
        gap = hp - ap
        if hp >= 62 and gap >= 25:
            return 2, 0, f'{home} heavy favorite: home={hp}% away={ap}%'
        if hp >= 50 and gap >= 12:
            return 2, 1, f'{home} favorite: home={hp}% away={ap}%'
        return 1, 0, f'{home} slight edge: home={hp}% away={ap}% draw={dp}%'

    gap = ap - hp
    if ap >= 62 and gap >= 25:
        return 0, 2, f'{away} heavy favorite: away={ap}% home={hp}%'
    if ap >= 50 and gap >= 12:
        return 1, 2, f'{away} favorite: away={ap}% home={hp}%'
    return 0, 1, f'{away} slight edge: away={ap}% home={hp}% draw={dp}%'


class WormCupAPI:
    def __init__(self, init_data, ref=DEFAULT_START):
        self.init = init_data
        self.ref = ref
        self.s = requests.Session(impersonate='chrome124')
        self.common = {
            'accept': 'application/json, text/plain, */*',
            'origin': 'https://wormcup.vercel.app',
            'referer': 'https://wormcup.vercel.app/',
            'user-agent': 'Mozilla/5.0 (Linux; Android 13; SM-G991B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Mobile Safari/537.36',
        }
        self.access = None
        self.user = None

    def _json(self, r):
        try:
            return r.json()
        except Exception:
            return {'_text': (r.text or '')[:500], '_status': r.status_code}

    def _unwrap(self, j):
        if isinstance(j, dict) and 'result' in j and isinstance(j['result'], dict):
            return j['result'].get('data', j['result'])
        if isinstance(j, dict) and 'data' in j:
            return j['data']
        return j

    def tma_headers(self):
        return {**self.common, 'authorization': 'tma ' + self.init}

    def bearer_headers(self):
        return {**self.common, 'authorization': 'Bearer ' + self.access, 'content-type': 'application/json'}

    def login(self):
        r = self.s.get(WC + '/api/users/me/', headers=self.tma_headers(), timeout=30)
        j = self._json(r)
        if r.status_code != 200:
            raise RuntimeError(f'users/me HTTP {r.status_code}: {j}')
        self.user = self._unwrap(j)
        addr = self.user['address']

        r = self.s.get(WORM + '/api/sign-in/', params={'address': addr, 'network_type': 2}, headers=self.common, timeout=30)
        nonce = self._unwrap(self._json(r))['nonce']
        issued = time.strftime('%Y-%m-%dT%H:%M:%S.000Z', time.gmtime())
        msg = (
            f"www.worm.wtf wants you to sign in with your Solana account:\n{addr}\n\n"
            f"Sign in with Solana to the app.\n\nURI: https://www.worm.wtf\n"
            f"Version: 1\nChain ID: 1\nNonce: {nonce}\nIssued At: {issued}"
        )
        r = self.s.post(
            WC + '/api/signing/sign/',
            headers={**self.tma_headers(), 'content-type': 'application/json'},
            json={'kind': 'worm_auth_message', 'payload': msg},
            timeout=30,
        )
        sig = self._unwrap(self._json(r))['signed_payload']
        payload = {'message': msg, 'signature': sig, 'address': addr, 'nonce': nonce}
        if self.ref:
            payload['invitation_code'] = self.ref
        r = self.s.post(WORM + '/api/sign-in/', headers={**self.common, 'content-type': 'application/json'}, json=payload, timeout=30)
        tok = self._unwrap(self._json(r))
        self.access = tok['access_token']
        return self.user

    def request(self, method, path, body=None, params=None, retries=4):
        url = WORM + path
        last = None
        for attempt in range(retries + 1):
            try:
                r = self.s.request(method, url, headers=self.bearer_headers(), json=body, params=params, timeout=30)
                last = (r.status_code, self._json(r), dict(r.headers))
                # WormCup backend sometimes returns transient 502/503/504. Retry them,
                # including POST play/predict; a successful duplicate prediction is protected
                # server-side by my_prediction checks and play is idempotent enough for tap count.
                if r.status_code not in (500, 502, 503, 504, 520, 522, 524):
                    return last
            except Exception as e:
                last = (0, {'_error': repr(e)}, {})
            if attempt < retries:
                wait = min(60, 5 * (attempt + 1))
                print(f'    {method} {path} transient {last[0]}, retry in {wait}s ({attempt+1}/{retries})')
                time.sleep(wait)
        return last

    def get(self, path, **params):
        code, j, _ = self.request('GET', path, params=params or None)
        return code, j

    def post(self, path, body=None):
        code, j, _ = self.request('POST', path, body=body or {})
        return code, j

    def dashboard(self):
        code, j = self.get('/api/worldcup/me/dashboard/')
        return code, self._unwrap(j)

    def checkin(self):
        return self.post('/api/worldcup/streak/check-in/')

    def play(self, n=-1, delay=DEFAULT_PLAY_DELAY, max_429_retries=3):
        # n=-1 => play all remaining based on dashboard, max 100 safety.
        if n < 0:
            _, d = self.dashboard()
            n = min(int(d.get('game', {}).get('plays_remaining', 0)), 100)
        ok = 0
        last = None
        rate_limited = 0
        for i in range(n):
            retry = 0
            while True:
                code, j, headers = self.request('POST', '/api/worldcup/game/play/', body={})
                last = (code, j)
                if code == 200:
                    ok += 1
                    break
                if code in (500, 502, 503, 504, 520, 522, 524):
                    # Already retried inside request(); stop play loop so we don't spam
                    # a sick backend and print a clear status to the user.
                    print(f'    play stopped: backend error {code} setelah retry, lanjut task lain')
                    return {'ok': ok, 'target': n, 'last': last, 'rate_limited': rate_limited, 'stopped': 'backend'}
                if code == 429 and retry < max_429_retries:
                    rate_limited += 1
                    retry_after = headers.get('retry-after') or headers.get('Retry-After')
                    try:
                        wait = float(retry_after) if retry_after else 0
                    except ValueError:
                        wait = 0
                    wait = max(wait, min(90, max(30, delay * (2 + retry))))
                    print(f'    429 rate-limit, wait {wait:.0f}s lalu retry ({retry+1}/{max_429_retries})')
                    time.sleep(wait)
                    retry += 1
                    continue
                return {'ok': ok, 'target': n, 'last': last, 'rate_limited': rate_limited}
            if i < n - 1 and delay > 0:
                time.sleep(delay + random.uniform(0, min(1.5, delay * 0.25)))
        return {'ok': ok, 'target': n, 'last': last, 'rate_limited': rate_limited}

    def matches(self):
        code, j = self.get('/api/worldcup/matches/', limit=100, offset=0)
        data = self._unwrap(j)
        if code != 200:
            print(f'    matches failed HTTP {code}: {short(j, 220)}')
            return code, []
        return code, data if isinstance(data, list) else data.get('data', data.get('items', []))

    def predict_open(self, max_count=3):
        code, matches = self.matches()
        made = []
        if code != 200:
            return made
        for m in matches:
            if len(made) >= max_count:
                break
            if m.get('pool', {}).get('status') != 'OPEN' or m.get('my_prediction'):
                continue
            hs, aas, reason = pick_score(m)
            body = {'condition_id': m['condition_id'], 'home_score': hs, 'away_score': aas}
            res = self.post('/api/worldcup/predictions/', body)
            made.append({
                'home': m.get('home', {}).get('name'),
                'away': m.get('away', {}).get('name'),
                'score': f'{hs}-{aas}',
                'reason': reason,
                'status': res[0],
                'body': res[1],
            })
        return made

    def portfolio(self):
        code, j = self.get('/api/worldcup/me/portfolio/')
        return code, self._unwrap(j)


def run_account(account, play_count=DEFAULT_PLAY_LIMIT, predict_max=DEFAULT_PREDICT_MAX, refresh=True, play_delay=DEFAULT_PLAY_DELAY):
    name = account['name']
    init = None
    if refresh:
        init = asyncio.run(get_webview(account, interactive_login=False))
    if not init:
        init = load_saved_init(name)
    if not init:
        raise RuntimeError(f'{name}: initData tidak ada')

    api = WormCupAPI(init, account.get('start_param') or DEFAULT_START)
    user = api.login()
    print(f'[{name}] auth OK tg={user.get("telegram_user_id")} addr={user.get("address", "-")[:6]}... ref={user.get("referred_by_referral_code")}')

    code, dash = api.dashboard()
    game = dash.get('game', {}) if isinstance(dash, dict) else {}
    print(f'[{name}] dashboard {code}: points={dash.get("points", {}).get("balance")} plays={game.get("plays_today")}/{game.get("daily_limit")} remaining={game.get("plays_remaining")} streak={dash.get("streak", {}).get("current")}')

    code, chk = api.checkin()
    print(f'[{name}] daily {code}: {short(chk)}')

    if play_count is not None:
        if play_count == 0:
            print(f'[{name}] play skipped')
        else:
            n = play_count if play_count > 0 else 0
            result = api.play(n=n, delay=play_delay)
            last_code = result['last'][0] if result.get('last') else None
            print(f'[{name}] play OK {result["ok"]}/{result["target"]} last={last_code} 429={result["rate_limited"]}')

    if predict_max and predict_max > 0:
        made = api.predict_open(max_count=predict_max)
        print(f'[{name}] predict submitted {len(made)} (strategy: API distribution heuristic)')
        for m in made:
            print(f'  - {m["home"]} vs {m["away"]}: {m["score"]} -> {m["status"]} | {m["reason"]}')
    else:
        print(f'[{name}] predict skipped')

    code, port = api.portfolio()
    if isinstance(port, dict):
        print(f'[{name}] portfolio {code}: pos={len(port.get("positions", []))} hist={len(port.get("history", []))} bal={port.get("balance_usdc")}')
    else:
        print(f'[{name}] portfolio {code}: {short(port)}')
    code, dash2 = api.dashboard()
    if isinstance(dash2, dict):
        g2 = dash2.get('game', {})
        print(f'[{name}] after: points={dash2.get("points", {}).get("balance")} plays={g2.get("plays_today")}/{g2.get("daily_limit")} remaining={g2.get("plays_remaining")}')
    else:
        print(f'[{name}] after dashboard {code}: {short(dash2)}')

def countdown(seconds, label='next loop'):
    seconds = int(seconds or 0)
    if seconds <= 0:
        return
    frames = ['|', '/', '-', '\\']
    end = time.time() + seconds
    i = 0
    while True:
        left = int(round(end - time.time()))
        if left <= 0:
            break
        sys.stdout.write(f'\r{frames[i % len(frames)]} jeda {label}: {left:>4}s (Ctrl+C stop)')
        sys.stdout.flush()
        time.sleep(1)
        i += 1
    sys.stdout.write('\r' + ' ' * 70 + '\r')
    sys.stdout.flush()


def run_targets_loop(targets, play_count=DEFAULT_PLAY_LIMIT, predict_max=DEFAULT_PREDICT_MAX, delay=DEFAULT_PLAY_DELAY, interval=0, refresh=True):
    cycle = 1
    while True:
        print(f'\n=== CYCLE {cycle} | accounts={len(targets)} | play={play_count} predict={predict_max} ===')
        for acc in targets:
            try:
                run_account(acc, play_count=play_count, predict_max=predict_max, refresh=refresh, play_delay=delay)
            except KeyboardInterrupt:
                raise
            except Exception as e:
                print(f'[{acc.get("name")}] ERROR {e}')
        if interval <= 0:
            break
        cycle += 1
        countdown(interval, 'loop berikutnya')


def prompt_int(label, default, allow_blank=True):
    raw = input(f'{label} [{default}]: ').strip()
    if allow_blank and raw == '':
        return default
    return int(raw)


def prompt_float(label, default):
    raw = input(f'{label} [{default}]: ').strip()
    return default if raw == '' else float(raw)


def menu():
    while True:
        print('\nWormCup API Runner (pure requests + Telethon, no Playwright)')
        print('1. Tambah/refresh account')
        print('2. Existing accounts: clear daily/play/predict')
        print('3. Existing one account')
        print('4. Show accounts')
        print('0. Exit')
        ch = input('Pilih: ').strip()
        if ch == '0':
            return
        accs = load_accounts()
        if ch == '1':
            name = input('Nama account [main]: ').strip() or 'main'
            phone = input('Phone lengkap (+62...): ').strip()
            start = input(f'Start/ref [{DEFAULT_START}]: ').strip() or DEFAULT_START
            acc = next((a for a in accs if a['name'] == name), None)
            if not acc:
                acc = {'name': name, 'phone': mask_phone(phone), 'session': f'sessions/{name}.session', 'start_param': start}
                accs.append(acc)
            acc['phone'] = phone
            acc['start_param'] = start
            asyncio.run(get_webview(acc, interactive_login=True))
            acc['phone'] = mask_phone(phone)
            save_accounts(accs)
            print('Saved accounts.json')
            run_now = input('Langsung clear task akun ini? [Y/n]: ').strip().lower()
            if run_now in ('', 'y', 'yes'):
                run_targets_loop([acc], play_count=DEFAULT_PLAY_LIMIT, predict_max=DEFAULT_PREDICT_MAX, delay=DEFAULT_PLAY_DELAY, interval=0, refresh=False)
        elif ch in ('2', '3'):
            if not accs:
                print('Belum ada accounts.json. Login dulu menu 1.')
                continue
            targets = accs
            if ch == '3':
                for i, a in enumerate(accs, 1):
                    print(i, a['name'], a.get('phone'))
                idx = int(input('No: ').strip()) - 1
                targets = [accs[idx]]
            print('Mode existing: refresh WebView, claim daily, play, predict, portfolio, lalu loop kalau interval > 0')
            print('Play count: -1/all remaining, 0/skip, N/jumlah tap')
            play_count = prompt_int('Play count', DEFAULT_PLAY_LIMIT)
            predict_max = prompt_int('Max predict open match', DEFAULT_PREDICT_MAX)
            delay = prompt_float('Delay antar tap detik (anti 429)', DEFAULT_PLAY_DELAY)
            interval = prompt_int('Loop interval detik (0 sekali jalan)', DEFAULT_LOOP_INTERVAL)
            run_targets_loop(targets, play_count=play_count, predict_max=predict_max, delay=delay, interval=interval, refresh=True)
        elif ch == '4':
            print(json.dumps(accs, indent=2))
        else:
            print('pilihan salah')


def cli():
    ap = argparse.ArgumentParser(description='WormCup pure API runner')
    ap.add_argument('--list', action='store_true', help='list accounts')
    ap.add_argument('--run-all', action='store_true', help='run all accounts')
    ap.add_argument('--run', metavar='NAME', help='run one account')
    ap.add_argument('--no-refresh', action='store_true', help='use saved initData, do not refresh Telegram WebView')
    ap.add_argument('--play-count', type=int, default=DEFAULT_PLAY_LIMIT, help='-1/all remaining, 0 skip, N taps')
    ap.add_argument('--predict-max', type=int, default=DEFAULT_PREDICT_MAX, help='0 skip, N open matches')
    ap.add_argument('--delay', type=float, default=DEFAULT_PLAY_DELAY, help='delay between play taps seconds')
    ap.add_argument('--loop', type=int, default=0, help='loop interval seconds; 0 one-shot')
    args = ap.parse_args()
    accs = load_accounts()
    if args.list:
        print(json.dumps(accs, indent=2))
        return
    if args.run_all or args.run:
        targets = accs if args.run_all else [a for a in accs if a.get('name') == args.run]
        if not targets:
            raise SystemExit('account not found / accounts.json kosong')
        run_targets_loop(
            targets,
            play_count=args.play_count,
            predict_max=args.predict_max,
            delay=args.delay,
            interval=args.loop,
            refresh=not args.no_refresh,
        )
        return
    menu()


if __name__ == '__main__':
    cli()

