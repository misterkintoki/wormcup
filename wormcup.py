#!/usr/bin/env python3
"""
WormCup — token-based multi-account automation.

Config via env vars or .env file. Token per baris di token.txt.

Usage:
  cp .env.example .env           # edit config
  echo "eyJhbG..." > token.txt   # paste token(s)
  python wormcup.py              # run
  python wormcup.py --plan       # preview predictions
  python wormcup.py --status     # cek token
"""
import argparse
import base64
import json
import os
import random
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from curl_cffi import requests

# ── Load .env ─────────────────────────────────────────────────────
def load_env():
    env_file = Path(__file__).parent / '.env'
    if not env_file.exists():
        return
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        if '=' in line:
            k, v = line.split('=', 1)
            k, v = k.strip(), v.strip().strip('"').strip("'")
            if k and k not in os.environ:  # don't override existing env
                os.environ[k] = v

load_env()

# ── Config ────────────────────────────────────────────────────────
WORM = os.getenv('WORMCUP_API', 'https://api.worm.wtf')
DELAY = int(os.getenv('WORMCUP_DELAY', '15'))
PREDICT_MAX = int(os.getenv('WORMCUP_PREDICT', '3'))
TOKEN_FILE = os.getenv('WORMCUP_TOKENS', str(Path(__file__).parent / 'token.txt'))
SPREAD = os.getenv('WORMCUP_SPREAD', 'true').lower() in ('true', '1', 'yes')

H = {
    'accept': 'application/json, text/plain, */*',
    'content-type': 'application/json',
    'origin': 'https://wormcup.vercel.app',
    'referer': 'https://wormcup.vercel.app/',
    'user-agent': 'Mozilla/5.0 (Linux; Android 13; SM-G991B) AppleWebKit/537.36 Chrome/126 Mobile Safari/537.36',
}


# ── Helpers ───────────────────────────────────────────────────────
def load_tokens():
    p = Path(TOKEN_FILE)
    if not p.exists():
        return []
    return [l.strip() for l in p.read_text().splitlines() if l.strip()]


def unwrap(j):
    if isinstance(j, dict):
        if 'result' in j and isinstance(j['result'], dict):
            return j['result'].get('data', j['result'])
        if 'data' in j:
            return j['data']
    return j


def jwt_info(tok):
    try:
        p = json.loads(base64.urlsafe_b64decode(tok.split('.')[1] + '=='))
        exp = datetime.fromtimestamp(p['exp'], tz=timezone.utc)
        hrs = max(0, (p['exp'] - time.time()) / 3600)
        return {'uid': p.get('user_id'), 'exp': exp, 'hrs': hrs}
    except:
        return {}


# ── API ───────────────────────────────────────────────────────────
class API:
    def __init__(self, tok):
        self.tok = tok
        self.s = requests.Session(impersonate='chrome124')

    def req(self, m, path, body=None, params=None, retries=3):
        url = WORM + path
        rh = {**H, 'authorization': 'Bearer ' + self.tok}
        for i in range(retries + 1):
            try:
                r = self.s.request(m, url, headers=rh, json=body, params=params, timeout=30)
                try: j = r.json()
                except: j = {'_t': r.text[:200]}
                if r.status_code not in (500, 502, 503, 504, 520, 522, 524):
                    return r.status_code, j
            except Exception as e:
                j = {'_e': str(e)}
                r = type('X', (), {'status_code': 0})()
            if i < retries:
                time.sleep(min(60, 5 * (i + 1)))
        return getattr(r, 'status_code', 0), j

    def get(self, p, **kw):
        c, j = self.req('GET', p, params=kw or None)
        return c, unwrap(j)

    def post(self, p, body=None):
        c, j = self.req('POST', p, body=body or {})
        return c, unwrap(j)

    def dashboard(self): return self.get('/api/worldcup/me/dashboard/')
    def checkin(self): return self.post('/api/worldcup/streak/check-in/')
    def portfolio(self): return self.get('/api/worldcup/me/portfolio/')

    def matches(self, auth=True):
        rh = {**H, 'authorization': 'Bearer ' + self.tok} if auth else H
        url = WORM + '/api/worldcup/matches/'
        for i in range(3):
            try:
                r = self.s.get(url, headers=rh, params={'limit': 200, 'offset': 0}, timeout=30)
                j = r.json()
                if r.status_code == 200:
                    data = unwrap(j)
                    items = data if isinstance(data, list) else data.get('data', data.get('items', []))
                    return 200, items
                return r.status_code, []
            except:
                if i < 2: time.sleep(5)
        return 0, []

    def play(self, n=-1, delay=DELAY):
        if n < 0:
            c, d = self.dashboard()
            n = min(int(d.get('game', {}).get('plays_remaining', 0)), 100) if c == 200 else 0
        ok = 0
        for i in range(n):
            retry = 0
            while True:
                c, j = self.req('POST', '/api/worldcup/game/play/')
                if c == 200: ok += 1; break
                if c == 429 and retry < 3:
                    w = max(30, min(90, delay * (2 + retry)))
                    print(f'    429 wait {w}s ({retry+1}/3)')
                    time.sleep(w); retry += 1; continue
                return {'ok': ok, 'target': n, 'err': c}
            if i < n - 1: time.sleep(delay + random.uniform(0, 1.5))
        return {'ok': ok, 'target': n}


# ── Score Strategy ────────────────────────────────────────────────
SCORES = [
    (1, 0), (0, 1), (1, 1), (0, 0), (2, 1), (1, 2),
    (2, 0), (0, 2), (2, 2), (3, 1), (1, 3), (3, 0), (0, 3),
    (3, 2), (2, 3), (4, 0), (0, 4), (4, 1), (1, 4), (4, 2), (2, 4),
]


def generate_candidates(match):
    d = match.get('distribution') or {}
    hp = int(d.get('home_pct') or 33)
    dp = int(d.get('draw_pct') or 33)
    ap = int(d.get('away_pct') or 33)
    home = match.get('home', {}).get('name', '?')
    away = match.get('away', {}).get('name', '?')

    c = []

    # Draw zone
    if dp >= 28 and abs(max(hp, dp, ap) - dp) <= 8:
        c.extend([(1, 1, f'draw zone D={dp}%'), (0, 0, f'0-0 draw'), (2, 2, f'2-2 open')])
    elif dp >= 34:
        c.extend([(0, 0, f'strong draw D={dp}%'), (1, 1, f'draw D={dp}%')])

    # Home/away primary
    if hp >= ap:
        g = hp - ap
        if hp >= 62 and g >= 25:
            c.extend([(2, 0, f'{home} heavy H={hp}%'), (3, 0, f'{home} dom'), (3, 1, f'{home} strong'), (1, 0, f'{home} tight'), (2, 1, f'{home} close')])
        elif hp >= 50 and g >= 12:
            c.extend([(2, 1, f'{home} fav H={hp}%'), (1, 0, f'{home} edge'), (2, 0, f'{home} clean'), (3, 1, f'{home} open')])
        else:
            c.extend([(1, 0, f'{home} slight H={hp}%'), (0, 0, f'low draw'), (2, 1, f'{home} tight'), (1, 1, f'balanced')])
    else:
        g = ap - hp
        if ap >= 62 and g >= 25:
            c.extend([(0, 2, f'{away} heavy A={ap}%'), (0, 3, f'{away} dom'), (1, 3, f'{away} strong'), (0, 1, f'{away} tight'), (1, 2, f'{away} close')])
        elif ap >= 50 and g >= 12:
            c.extend([(1, 2, f'{away} fav A={ap}%'), (0, 1, f'{away} edge'), (0, 2, f'{away} clean'), (1, 3, f'{away} open')])
        else:
            c.extend([(0, 1, f'{away} slight A={ap}%'), (0, 0, f'low draw'), (1, 2, f'{away} tight'), (1, 1, f'balanced')])

    # Counter-trend (contrarian)
    if hp >= 60:
        c.extend([(0, 0, f'CT draw vs {hp}%'), (1, 1, f'CT draw vs {hp}%'), (0, 1, f'CT away vs {hp}%')])
    if ap >= 60:
        c.extend([(0, 0, f'CT draw vs {ap}%'), (1, 0, f'CT home vs {ap}%'), (1, 1, f'CT draw vs {ap}%')])
    if dp >= 40:
        c.extend([(1, 0, f'CT decisive vs {dp}%'), (0, 1, f'CT decisive vs {dp}%')])

    # Dedupe + fill
    seen = set()
    out = []
    for item in c:
        k = (item[0], item[1])
        if k not in seen:
            seen.add(k); out.append(item)
    for s in SCORES:
        if len(out) >= 15: break
        if s not in seen:
            seen.add(s); out.append((s[0], s[1], f'fallback {s[0]}-{s[1]}'))
    return out


def assign_predictions(matches, n_accounts, max_per_account):
    open_m = [m for m in matches if m.get('pool', {}).get('status') == 'OPEN' and not m.get('my_prediction')]
    open_m.sort(key=lambda m: m.get('pool', {}).get('predictor_count', 999999))
    open_m = open_m[:max_per_account]

    assignments = {i: [] for i in range(n_accounts)}
    for match in open_m:
        candidates = generate_candidates(match)
        if not candidates: continue
        for acc in range(n_accounts):
            if acc < len(candidates):
                hs, aws, reason = candidates[acc]
            else:
                hs, aws, reason = random.choice(candidates[5:]) if len(candidates) > 5 else random.choice(candidates)
            assignments[acc].append((match, hs, aws, reason))
    return assignments


def print_plan(assignments, n_accounts):
    print(f'\n📋 PREDICTION PLAN ({n_accounts} accounts)')
    print('─' * 70)
    match_map = {}
    for acc in assignments:
        for match, hs, aws, reason in assignments[acc]:
            cid = match['condition_id']
            if cid not in match_map:
                match_map[cid] = {'match': match, 'picks': []}
            match_map[cid]['picks'].append({'acc': f'acc{acc+1}', 'score': f'{hs}-{aws}', 'reason': reason})

    for cid, info in match_map.items():
        m = info['match']
        pool = m.get('pool', {})
        dist = m.get('distribution') or {}
        print(f'\n⚽ {m["home"]["name"]} vs {m["away"]["name"]} | pool=${pool.get("amount_usdc","?")} preds={pool.get("predictor_count","?")}')
        print(f'   dist: H={dist.get("home_pct","?")}% D={dist.get("draw_pct","?")}% A={dist.get("away_pct","?")}%')
        for p in info['picks']:
            print(f'   {p["acc"]:>5} → {p["score"]:>5}  {p["reason"]}')


# ── Run ───────────────────────────────────────────────────────────
def run_account(idx, tok, play, predict, delay, assignments=None):
    tag = f'acc{idx+1}'
    info = jwt_info(tok)
    api = API(tok)

    c, d = api.dashboard()
    if c != 200:
        print(f'[{tag}] ❌ dead (HTTP {c}) uid={info.get("uid","?")}')
        return False

    g = d.get('game', {}) if isinstance(d, dict) else {}
    s = d.get('streak', {}) if isinstance(d, dict) else {}
    print(f'[{tag}] uid={info.get("uid","?")} pts={d.get("points",{}).get("balance","?")} rem={g.get("plays_remaining","?")} streak={s.get("current","?")} token={info.get("hrs",0):.0f}h')

    api.checkin()

    if play != 0:
        r = api.play(n=play, delay=delay)
        print(f'[{tag}] play {r["ok"]}/{r["target"]}')

    if assignments and idx in assignments:
        picks = assignments[idx]
        made = 0
        for match, hs, aws, reason in picks:
            c, _ = api.post('/api/worldcup/predictions/', {
                'condition_id': match['condition_id'], 'home_score': hs, 'away_score': aws,
            })
            if c == 200: made += 1
            print(f'  {"✅" if c==200 else f"❌{c}"} {match["home"]["name"]} vs {match["away"]["name"]}: {hs}-{aws} | {reason}')
        print(f'[{tag}] predicted {made}/{len(picks)}')

    c, d2 = api.dashboard()
    if c == 200 and isinstance(d2, dict):
        print(f'[{tag}] after: pts={d2.get("points",{}).get("balance")} rem={d2.get("game",{}).get("plays_remaining")}')

    c, p = api.portfolio()
    if c == 200 and isinstance(p, dict):
        print(f'[{tag}] portfolio: pos={len(p.get("positions",[]))} usdc={p.get("balance_usdc","?")}')

    return True


def countdown(s):
    end = time.time() + s; i = 0
    chars = '|/-\\'
    while True:
        l = int(end - time.time())
        if l <= 0: break
        sys.stdout.write(f'\r{chars[i%4]} next: {l}s  ')
        sys.stdout.flush(); time.sleep(1); i += 1
    sys.stdout.write('\r' + ' ' * 25 + '\r')


def cli():
    ap = argparse.ArgumentParser()
    ap.add_argument('--status', action='store_true')
    ap.add_argument('--plan', action='store_true')
    ap.add_argument('--play', type=int, default=-1)
    ap.add_argument('--predict', type=int, default=PREDICT_MAX)
    ap.add_argument('--delay', type=float, default=DELAY)
    ap.add_argument('--loop', type=int, default=0)
    args = ap.parse_args()

    tokens = load_tokens()
    if not tokens:
        print(f'token.txt kosong ({TOKEN_FILE})')
        print('Paste token per baris.')
        return

    if args.status:
        print(f'\n{"#":<4} {"UID":<10} {"TOKEN":<10} {"EXPIRES":<12}')
        print('─' * 38)
        for i, tok in enumerate(tokens):
            info = jwt_info(tok)
            exp = info.get('exp')
            print(f'{i+1:<4} {str(info.get("uid","?")):<10} {info.get("hrs",0):>5.0f}h     {exp.strftime("%m-%d %H:%M") if exp else "?"}')
        return

    cycle = 1
    while True:
        print(f'\n═══ CYCLE {cycle} | {len(tokens)} acct(s) | play={args.play} predict={args.predict} spread={SPREAD} ═══')

        assignments = None
        if args.predict > 0:
            api = API(tokens[0])
            c, matches = api.matches(auth=False)
            if c == 200:
                if SPREAD:
                    assignments = assign_predictions(matches, len(tokens), args.predict)
                    print_plan(assignments, len(tokens))
                else:
                    # Same prediction for all accounts
                    assignments = assign_predictions(matches, 1, args.predict)
                    assignments = {i: assignments[0] for i in range(len(tokens))}
                    print_plan(assignments, len(tokens))
            else:
                print(f'  ⚠️ matches failed (HTTP {c})')

        if args.plan:
            return

        for i, tok in enumerate(tokens):
            try:
                run_account(i, tok, args.play, args.predict, args.delay, assignments)
            except KeyboardInterrupt: raise
            except Exception as e:
                print(f'[acc{i+1}] ERR: {e}')

        if args.loop <= 0: break
        cycle += 1; countdown(args.loop)


if __name__ == '__main__':
    cli()
