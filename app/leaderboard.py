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

from .db import set_leaderboard_cache, get_leaderboard_cache

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
# Строку из прошлого кеша, которую в этот раз не удалось пересобрать (недобор
# HL из-за троттлинга), храним не дольше этого срока — иначе делистнутые/мёртвые
# инструменты висели бы вечно. За 1-2 обновления недобор обычно закрывается.
STALE_KEEP_DAYS = 10
# Единый лимит конкурентности. Важный урок (см. Инструкцию, Блок 39): агрессивные
# ретраи HL дают ШТОРМ — упавшие чанки повторяются пачкой и сами поддерживают
# throttle, из-за чего полный сбор захлёбывался (до 63/88 HL с n=0). Быстрый
# best-effort с лёгким ретраем (2 попытки) отрабатывает за ~50с и берёт ~90%+
# HL; недобор конкретных инструментов случаен и добирается повторным «Обновить».
CONCURRENCY = 12

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
# Перечисление — всего 3 запроса, но критичные: если один зафейлит (троттлинг),
# универсум усечётся и склейка не сможет добрать пропавшие инструменты. Поэтому
# у них свой упорный ретрай, и при полном провале — исключение (лучше уронить
# обновление, чем молча собрать неполный список).

async def _enum_get(client, url, params=None):
    for attempt in range(5):
        try:
            r = await client.get(url, params=params, timeout=25.0)
            if r.status_code == 200:
                return r.json()
        except Exception:
            pass
        await asyncio.sleep(0.5 * (attempt + 1))
    raise RuntimeError(f'enum GET failed: {url}')


async def _enum_post(client, url, body):
    for attempt in range(5):
        try:
            r = await client.post(url, json=body, timeout=25.0)
            if r.status_code == 200:
                return r.json()
        except Exception:
            pass
        await asyncio.sleep(0.5 * (attempt + 1))
    raise RuntimeError(f'enum POST failed: {url}')


async def _binance_universe(client: httpx.AsyncClient) -> list[dict]:
    data = (await _enum_get(client, BINANCE_EXCHANGE_INFO_URL)).get('symbols', [])
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
    data = await _enum_post(client, HL_URL, {'type': 'meta', 'dex': 'xyz'})
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
    data = (await _enum_get(client, OKX_INSTRUMENTS_URL, {'instType': 'SWAP'})).get('data', [])
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

# Отдельные семафоры на биржу: HL из датацентра Railway троттлит агрессивнее
# (часовой фандинг = сотни запросов), поэтому его конкурентность держим ниже,
# чтобы не ловить массовый 429; OKX/Binance терпят больше. Единый семафор на
# всё раньше давал HL захлёбываться в общем залпе (63/88 инструментов с n=0).
_SEM: asyncio.Semaphore | None = None


async def _get(client, url, params, retries=2):
    for attempt in range(retries + 1):
        try:
            async with _SEM:
                r = await client.get(url, params=params, timeout=25.0)
            if r.status_code == 200:
                return r
            if r.status_code == 429 or r.status_code >= 500:
                await asyncio.sleep(0.3 * (attempt + 1))
                continue
            return r
        except Exception:
            if attempt == retries:
                return None
            await asyncio.sleep(0.3 * (attempt + 1))
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


async def _hl_chunk(client, coin, dex, cs, ce, retries=2) -> list[tuple[int, float]]:
    # Лёгкий ретрай (2 попытки): HL иногда отвечает 200 с телом-ошибкой при
    # троттлинге. Агрессивнее ретраить НЕЛЬЗЯ — даёт шторм (см. коммент выше).
    for attempt in range(retries + 1):
        try:
            async with _SEM:
                r = await client.post(HL_URL, json={
                    'type': 'fundingHistory', 'coin': coin, 'dex': dex,
                    'startTime': cs, 'endTime': ce}, timeout=25.0)
            if r.status_code == 200:
                data = r.json()
                if isinstance(data, list):
                    return [(int(d['time']), float(d['fundingRate'])) for d in data]
        except Exception:
            pass
        if attempt < retries:
            await asyncio.sleep(0.3 * (attempt + 1))
    return []


async def _hl_funding(client, coin, dex, start_ms, end_ms) -> list[tuple[int, float]]:
    # Чанки тянем ПОСЛЕДОВАТЕЛЬНО (по одному на инструмент за раз): параллельный
    # залп из ~10 чанков на монету бьёт HL пачкой и сильнее срывает throttle
    # (проверено: параллельно 26/88 HL, последовательно ~73/88). Полноту до
    # 100% добирает склейка с прошлым кешем между обновлениями.
    seen, rows = set(), []
    cs = start_ms
    while cs < end_ms:
        ce = min(cs + HL_CHUNK_MS, end_ms)
        for ts, rate in await _hl_chunk(client, coin, dex, cs, ce):
            if ts not in seen:
                seen.add(ts)
                rows.append((ts, rate))
        cs = ce + 1
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

            fresh = {}   # (exchange, symbol) -> строка, успешно собранная СЕЙЧАС

            async def work(item):
                try:
                    pts = await _fetch_funding(client, item, start_ms, now_ms)
                    acc = _accumulate(pts, now_ms)
                    # Кладём только строки, где реально есть данные хоть в одном
                    # периоде. Инструменты с недобором (все n=0) в этот раз
                    # пропускаем — их закроет склейка с прошлым кешем ниже.
                    if any(acc[p]['n'] > 0 for p in PERIODS):
                        fresh[(item['exchange'], item['symbol'])] = {
                            'exchange': item['exchange'], 'symbol': item['symbol'],
                            'base': item['base'], 'cls': item['cls'], 'periods': acc,
                            'updated_at': now_ms,
                        }
                except Exception as e:
                    print(f"leaderboard {item['exchange']}:{item['symbol']} err: {e}")
                finally:
                    status['progress'] += 1

            await asyncio.gather(*(work(i) for i in universe))

        # Склейка с прошлым кешем (Блок 39): HL из датацентра троттлит, за один
        # проход добирается ~73/88 инструментов (случайный недобор). Вместо того
        # чтобы показывать неполный рейтинг, берём свежие строки, а для тех, что
        # в этот раз не собрались, — оставляем последнее известное значение из
        # прошлого кеша (не старше STALE_KEEP_DAYS). За 1-2 «Обновить» HL
        # добирается до 100%. Это остаётся «только рейтингом» — храним лишь
        # посчитанные строки, не сырую историю фандинга.
        merged = {}
        prev = await get_leaderboard_cache('latest')
        if prev:
            for row in json.loads(prev['data']).get('rows', []):
                if now_ms - row.get('updated_at', 0) <= STALE_KEEP_DAYS * 86400000:
                    merged[(row['exchange'], row['symbol'])] = row
        merged.update(fresh)   # свежие перекрывают старые
        rows = list(merged.values())

        payload = {'rows': rows, 'periods': list(PERIODS.keys()),
                   'computed_at': now_ms, 'universe_size': len(universe),
                   'fresh_count': len(fresh)}
        await set_leaderboard_cache('latest', json.dumps(payload, ensure_ascii=False))
        status['last_updated'] = now_ms
        return payload
    except Exception as e:
        print(traceback.format_exc())
        status['error'] = f'{type(e).__name__}: {e}'
        raise
    finally:
        status.update(running=False, finished_at=_now_ms())
