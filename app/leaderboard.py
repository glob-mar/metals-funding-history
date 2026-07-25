"""Топ по накопленному фандингу (Блок 39) — отдельный блок-лидерборд, который
сам собирает универсум НЕкрипто-инструментов (металлы + акции) с трёх бирж и
ранжирует их по накопленному фандингу за период, БЕЗ ручного добавления
каждого актива. Храним только готовый рейтинг (набор строк со суммами по всем
периодам сразу), не всю историю фандинга — пересчёт по кнопке «Обновить».

Как отличаем некрипту (проверено на живых API):
- Binance: родное поле underlyingType = EQUITY (акции) / COMMODITY (металлы,
  нефть) / COIN (крипта) — берём EQUITY и COMMODITY.
- Hyperliquid: некрипта живёт в отдельном dex 'xyz' — берём его живые
  инструменты (isDelisted=false).
- OKX: своего поля класса нет (NVDA/XAU/BTC структурно одинаковы), поэтому
  берём -USDT-SWAP, чей базовый тикер есть в некрипто-множестве, собранном
  из Binance+Hyperliquid (перекрёстная сверка, без ручного справочника).
"""
import asyncio
import time
import json
import traceback
import httpx

from .db import set_leaderboard_cache

OKX_FUNDING_URL = 'https://www.okx.com/api/v5/public/funding-rate-history'
OKX_INSTRUMENTS_URL = 'https://www.okx.com/api/v5/public/instruments'
BINANCE_FUNDING_URL = 'https://fapi.binance.com/fapi/v1/fundingRate'
BINANCE_EXCHANGE_INFO_URL = 'https://fapi.binance.com/fapi/v1/exchangeInfo'
HL_URL = 'https://api.hyperliquid.xyz/info'

# Часовой фандинг у HL даёт много точек — берём куски по 20 дней (20*24=480 < 500,
# лимит ответа HL), чтобы сократить число запросов против дефолтных 7-дневных.
HL_CHUNK_MS = 20 * 24 * 3600 * 1000

WINDOW_DAYS = 190          # общее окно выгрузки (с запасом на 6 мес)
PERIODS = {'1m': 30, '2m': 60, '3m': 90, '6m': 182}
CONCURRENCY = 20

# Металлы по нормализованному тикеру (у разных бирж свои имена одного металла).
METAL_ALIASES = {
    'GOLD': 'XAU', 'XAU': 'XAU',
    'SILVER': 'XAG', 'XAG': 'XAG',
    'PLATINUM': 'XPT', 'XPT': 'XPT',
    'PALLADIUM': 'XPD', 'XPD': 'XPD',
    'COPPER': 'XCU', 'XCU': 'XCU',
}
# ТОЧНЫЙ набор энергетических тикеров (не подстрока! иначе ORCL/CRCL ловились
# на 'CL', AXTI — на 'XTI'). Основной источник класса — карта от Binance
# (underlyingType), этот набор нужен лишь для HL/OKX-only тикеров, которых нет
# на Binance (напр. HL 'BRENTOIL').
COMMODITY_EXACT = {'CL', 'BZ', 'WTI', 'BRENT', 'BRENTOIL', 'NATGAS', 'NGAS', 'OIL', 'GAS', 'XNG', 'XBR', 'XTI'}

status = {
    'running': False,
    'started_at': None,
    'finished_at': None,
    'progress': 0,
    'total': 0,
    'error': None,
    'last_updated': None,
}


def _now_ms() -> int:
    return int(time.time() * 1000)


def _classify_binance(base: str, underlying_type: str) -> str:
    """Класс для Binance-инструмента напрямую из underlyingType (authoritative).
    Металлы выделяем отдельно от прочих COMMODITY (энергии)."""
    if base.upper() in METAL_ALIASES:
        return 'metal'
    if underlying_type == 'COMMODITY':
        return 'commodity'
    return 'stock'  # EQUITY


def _classify_ref(base: str, binance_class: dict) -> str:
    """Класс для HL/OKX-инструмента: сначала металлы (точный алиас), потом
    authoritative-карта от Binance по базовому тикеру, потом точный набор
    энергии, иначе акция. Никакого подстрочного матчинга."""
    norm = base.upper()
    if norm in METAL_ALIASES:
        return 'metal'
    if norm in binance_class:
        return binance_class[norm]
    if norm in COMMODITY_EXACT:
        return 'commodity'
    return 'stock'


# ── Универсум ──────────────────────────────────────────────────────────────

async def _binance_universe(client: httpx.AsyncClient) -> list[dict]:
    r = await client.get(BINANCE_EXCHANGE_INFO_URL, timeout=20.0)
    data = r.json().get('symbols', [])
    out = []
    for s in data:
        if s.get('status') != 'TRADING':
            continue
        ut = s.get('underlyingType')
        if ut not in ('EQUITY', 'COMMODITY'):
            continue
        sym = s['symbol']
        if not sym.endswith('USDT'):
            continue
        base = sym[:-4]
        out.append({'exchange': 'binance', 'symbol': sym, 'base': base,
                    'cls': _classify_binance(base, ut)})
    return out


async def _hl_universe(client: httpx.AsyncClient, binance_class: dict) -> list[dict]:
    r = await client.post(HL_URL, json={'type': 'meta', 'dex': 'xyz'}, timeout=20.0)
    data = r.json()
    uni = data.get('universe', []) if isinstance(data, dict) else []
    out = []
    for u in uni:
        if u.get('isDelisted'):
            continue
        name = u['name']                       # напр. 'xyz:NVDA'
        base = name.split(':', 1)[1] if ':' in name else name
        out.append({'exchange': 'hyperliquid', 'symbol': name, 'base': base,
                    'cls': _classify_ref(base, binance_class), 'dex': 'xyz'})
    return out


async def _okx_universe(client: httpx.AsyncClient, noncrypto_bases: set[str], binance_class: dict) -> list[dict]:
    r = await client.get(OKX_INSTRUMENTS_URL, params={'instType': 'SWAP'}, timeout=20.0)
    data = r.json().get('data', [])
    out = []
    for d in data:
        if d.get('state') != 'live' or not d['instId'].endswith('-USDT-SWAP'):
            continue
        base = d['instId'].split('-', 1)[0]
        if base.upper() not in noncrypto_bases:
            continue
        out.append({'exchange': 'okx', 'symbol': d['instId'], 'base': base,
                    'cls': _classify_ref(base, binance_class)})
    return out


# ── Сбор фандинга по сырому символу за окно ────────────────────────────────
# Универсум большой (~330 инструментов, ~1600 HTTP-запросов с пагинацией).
# С клиентских IP биржи почти не троттлят, но из датацентра Railway латентность
# выше и бывают 429/5xx. Ключевая оптимизация: единый семафор _SEM ограничивает
# ОБЩУЮ конкурентность на уровне отдельных запросов (а не инструментов), а
# HL-чанки (независимые окна времени) тянутся параллельно, а не по одному —
# иначе часовой фандинг HL (10 чанков на инструмент последовательно) был
# бутылочным горлышком. Каждый запрос — с быстрым лёгким ретраем на 429/5xx.

_SEM: asyncio.Semaphore | None = None


async def _get(client, url, params, retries=4):
    for attempt in range(retries + 1):
        try:
            async with _SEM:
                r = await client.get(url, params=params, timeout=20.0)
            if r.status_code == 200:
                return r
            if r.status_code == 429 or r.status_code >= 500:
                await asyncio.sleep(min(0.25 * (attempt + 1), 1.0))
                continue
            return r
        except Exception:
            if attempt == retries:
                return None
            await asyncio.sleep(min(0.25 * (attempt + 1), 1.0))
    return None


async def _post(client, url, body, retries=4):
    for attempt in range(retries + 1):
        try:
            async with _SEM:
                r = await client.post(url, json=body, timeout=20.0)
            if r.status_code == 200:
                return r
            if r.status_code == 429 or r.status_code >= 500:
                await asyncio.sleep(min(0.25 * (attempt + 1), 1.0))
                continue
            return r
        except Exception:
            if attempt == retries:
                return None
            await asyncio.sleep(min(0.25 * (attempt + 1), 1.0))
    return None


async def _binance_funding(client, symbol, start_ms, end_ms) -> list[tuple[int, float]]:
    rows, start = [], start_ms
    while start < end_ms:
        r = await _get(client, BINANCE_FUNDING_URL, {
            'symbol': symbol, 'startTime': start, 'endTime': end_ms, 'limit': 1000})
        if r is None or r.status_code != 200:
            break
        data = r.json()
        if not isinstance(data, list) or not data:
            break
        for d in data:
            rows.append((int(d['fundingTime']), float(d['fundingRate'])))
        if len(data) < 1000:
            break
        start = int(data[-1]['fundingTime']) + 1
    return rows


async def _okx_funding(client, inst, start_ms, end_ms) -> list[tuple[int, float]]:
    rows, after = [], None
    while True:
        params = {'instId': inst, 'limit': 100}
        if after:
            params['after'] = str(after)
        r = await _get(client, OKX_FUNDING_URL, params)
        if r is None or r.status_code != 200:
            break
        data = r.json().get('data', [])
        if not data:
            break
        stop = False
        for d in data:
            ft = int(d['fundingTime'])
            if ft < start_ms:
                stop = True
                break
            rows.append((ft, float(d['fundingRate'])))
        if stop or len(data) < 100:
            break
        after = min(int(d['fundingTime']) for d in data)
    return rows


async def _hl_chunk(client, coin, dex, cs, ce) -> list[tuple[int, float]]:
    r = await _post(client, HL_URL, {
        'type': 'fundingHistory', 'coin': coin, 'dex': dex, 'startTime': cs, 'endTime': ce})
    out = []
    if r is not None and r.status_code == 200:
        data = r.json()
        if isinstance(data, list):
            out = [(int(d['time']), float(d['fundingRate'])) for d in data]
    return out


async def _hl_funding(client, coin, dex, start_ms, end_ms) -> list[tuple[int, float]]:
    # Чанки — независимые окна времени, тянем их ПАРАЛЛЕЛЬНО (общий лимит держит
    # _SEM), а не по одному: это убирает главное бутылочное горлышко HL.
    windows = []
    cs = start_ms
    while cs < end_ms:
        ce = min(cs + HL_CHUNK_MS, end_ms)
        windows.append((cs, ce))
        cs = ce + 1
    chunks = await asyncio.gather(*(_hl_chunk(client, coin, dex, cs, ce) for cs, ce in windows))
    seen, rows = set(), []
    for chunk in chunks:
        for ts, rate in chunk:
            if ts in seen:
                continue
            seen.add(ts)
            rows.append((ts, rate))
    return rows


async def _fetch_funding(client, item, start_ms, end_ms) -> list[tuple[int, float]]:
    ex = item['exchange']
    if ex == 'binance':
        return await _binance_funding(client, item['symbol'], start_ms, end_ms)
    if ex == 'okx':
        return await _okx_funding(client, item['symbol'], start_ms, end_ms)
    if ex == 'hyperliquid':
        return await _hl_funding(client, item['symbol'], item.get('dex', 'xyz'), start_ms, end_ms)
    return []


# ── Накопление по периодам ─────────────────────────────────────────────────

def _accumulate(points: list[tuple[int, float]], now_ms: int) -> dict:
    """По списку (ts, rate) считает по каждому периоду: сумму ставок (в %),
    APR (годовая экстраполяция по реально покрытому периоду) и число точек."""
    out = {}
    for name, days in PERIODS.items():
        cutoff = now_ms - days * 86400000
        pts = [(t, r) for t, r in points if t >= cutoff]
        if not pts:
            out[name] = {'sum_pct': 0.0, 'apr': None, 'n': 0}
            continue
        s = sum(r for _, r in pts) * 100.0
        span_days = (max(t for t, _ in pts) - min(t for t, _ in pts)) / 86400000
        apr = (s / span_days * 365) if span_days >= 1 else None
        out[name] = {'sum_pct': round(s, 4), 'apr': round(apr, 2) if apr is not None else None, 'n': len(pts)}
    return out


# ── Оркестрация ────────────────────────────────────────────────────────────

async def refresh_leaderboard() -> dict:
    status.update(running=True, started_at=_now_ms(), finished_at=None,
                  progress=0, total=0, error=None)
    global _SEM
    _SEM = asyncio.Semaphore(CONCURRENCY)
    now_ms = _now_ms()
    start_ms = now_ms - WINDOW_DAYS * 86400000
    try:
        limits = httpx.Limits(max_connections=CONCURRENCY + 4, max_keepalive_connections=CONCURRENCY + 4)
        async with httpx.AsyncClient(headers={'User-Agent': 'metals-funding-history/1.0'}, limits=limits) as client:
            binance = await _binance_universe(client)
            binance_class = {i['base'].upper(): i['cls'] for i in binance}
            hl = await _hl_universe(client, binance_class)
            noncrypto_bases = {i['base'].upper() for i in binance} | {i['base'].upper() for i in hl}
            okx = await _okx_universe(client, noncrypto_bases, binance_class)
            universe = binance + hl + okx
            status['total'] = len(universe)

            rows = []

            # Конкурентность держит _SEM на уровне отдельных HTTP-запросов, поэтому
            # сами инструменты можно запускать все разом — HL-чанки внутри тоже
            # уходят в общий пул и параллелятся между инструментами.
            async def work(item):
                try:
                    pts = await _fetch_funding(client, item, start_ms, now_ms)
                    acc = _accumulate(pts, now_ms)
                    rows.append({
                        'exchange': item['exchange'], 'symbol': item['symbol'],
                        'base': item['base'], 'cls': item['cls'], 'periods': acc,
                    })
                except Exception as e:
                    print(f"leaderboard {item['exchange']}:{item['symbol']} err: {e}")
                finally:
                    status['progress'] += 1

            await asyncio.gather(*(work(i) for i in universe))

        payload = {'rows': rows, 'periods': list(PERIODS.keys()), 'computed_at': now_ms}
        await set_leaderboard_cache('latest', json.dumps(payload, ensure_ascii=False))
        status['last_updated'] = now_ms
        return payload
    except Exception as e:
        print(traceback.format_exc())
        status['error'] = f'{type(e).__name__}: {e}'
        raise
    finally:
        status.update(running=False, finished_at=_now_ms())
