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

from .db import set_leaderboard_cache, get_leaderboard_cache, get_vantage_symbols, get_all_assets

OKX_FUNDING_URL = 'https://www.okx.com/api/v5/public/funding-rate-history'
OKX_INSTRUMENTS_URL = 'https://www.okx.com/api/v5/public/instruments'
OKX_OI_URL = 'https://www.okx.com/api/v5/public/open-interest'
OKX_TICKERS_URL = 'https://www.okx.com/api/v5/market/tickers'
BINANCE_FUNDING_URL = 'https://fapi.binance.com/fapi/v1/fundingRate'
BINANCE_EXCHANGE_INFO_URL = 'https://fapi.binance.com/fapi/v1/exchangeInfo'
BINANCE_TICKER24_URL = 'https://fapi.binance.com/fapi/v1/ticker/24hr'
BINANCE_OI_URL = 'https://fapi.binance.com/fapi/v1/openInterest'
HL_URL = 'https://api.hyperliquid.xyz/info'

# Часовой фандинг у HL даёт много точек — берём куски по 20 дней (20*24=480 < 500,
# лимит ответа HL), чтобы сократить число запросов против дефолтных 7-дневных.
HL_CHUNK_MS = 20 * 24 * 3600 * 1000

WINDOW_DAYS = 190          # общее окно выгрузки (с запасом на 6 мес)
# С Блока 40 период НЕ фиксирован: храним по каждому инструменту компактный
# дневной ряд фандинга (d0 + dv), а любую сумму за произвольное окно [с..по]
# считает фронт на клиенте. Это остаётся «только рейтингом» — сырую поминутную
# историю фандинга не храним, лишь суточные суммы ставок.
DAY_MS = 86400000

# Режимы свопа MQL5 (см. Инструкцию Блок 33 и analysis.js) — своп у Vantage
# либо в пунктах (металлы/EUR), либо в годовых % (акции, банковский год 360),
# либо выключен. EA с Блока 33 шлёт имя режима строкой (EnumToString), старый
# кэш мог быть числом — поддерживаем оба.
SWAP_MODE_DISABLED = {0, 'SYMBOL_SWAP_MODE_DISABLED'}
SWAP_MODE_POINTS = {1, 'SYMBOL_SWAP_MODE_POINTS'}
SWAP_MODE_ANNUAL_PCT = {5, 'SYMBOL_SWAP_MODE_INTEREST_CURRENT'}
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


# ── Дневной ряд фандинга (Блок 40) ─────────────────────────────────────────

def _daily(points: list[tuple[int, float]]) -> tuple[int | None, list]:
    """Свёртка (ts, rate) в суточные суммы ставок (в %). Возвращает (d0, dv):
    d0 — номер суток (epoch-день) первого дня с данными, dv — плоский массив
    суточных сумм от d0 до последнего дня включительно; дни без данных — None
    (чтобы отличать реальный пропуск от честного нуля и точно считать покрытие).
    Из этого фронт складывает сумму за любое окно [с..по] сам."""
    if not points:
        return None, []
    buckets: dict[int, float] = {}
    for t, r in points:
        day = t // DAY_MS
        buckets[day] = buckets.get(day, 0.0) + r
    d0, d1 = min(buckets), max(buckets)
    dv = []
    for day in range(d0, d1 + 1):
        v = buckets.get(day)
        dv.append(round(v * 100.0, 5) if v is not None else None)
    return d0, dv


# ── Ликвидность: OI + оборот 24ч в $ (Блок 40) ─────────────────────────────
# Прокси глубины стакана — «сколько денег в инструменте» и суточный оборот.
# Голый фандинг бесполезен, если в инструмент не влезть объёмом. Тянем
# батч-эндпоинтами, где можно (OKX/HL — 1-2 запроса на всё), Binance OI —
# поштучно (батча нет на fapi), но только по некрипто-универсуму (~130 шт).

async def _binance_liquidity(client, symbols: set[str]) -> dict:
    out: dict = {}
    r = await _get(client, BINANCE_TICKER24_URL, None)
    tick = {}
    if r is not None and r.status_code == 200:
        body = r.json()
        if isinstance(body, list):
            tick = {d['symbol']: d for d in body}

    async def one(sym):
        t = tick.get(sym)
        price = float(t['lastPrice']) if t and t.get('lastPrice') else None
        vol = float(t['quoteVolume']) if t and t.get('quoteVolume') else None
        oi = None
        ro = await _get(client, BINANCE_OI_URL, {'symbol': sym})
        if ro is not None and ro.status_code == 200:
            try:
                oi_c = float(ro.json().get('openInterest'))
                oi = oi_c * price if price else None
            except Exception:
                pass
        out[('binance', sym)] = {'oi': oi, 'vol': vol, 'price': price}

    await asyncio.gather(*(one(s) for s in symbols))
    return out


async def _okx_liquidity(client, insts: set[str]) -> dict:
    oi_map, tk_map = {}, {}
    r_oi = await _get(client, OKX_OI_URL, {'instType': 'SWAP'})
    if r_oi is not None and r_oi.status_code == 200:
        oi_map = {d['instId']: d for d in r_oi.json().get('data', [])}
    r_tk = await _get(client, OKX_TICKERS_URL, {'instType': 'SWAP'})
    if r_tk is not None and r_tk.status_code == 200:
        tk_map = {d['instId']: d for d in r_tk.json().get('data', [])}
    out: dict = {}
    for inst in insts:
        tk, oi = tk_map.get(inst), oi_map.get(inst)
        price = float(tk['last']) if tk and tk.get('last') else None
        vol = float(tk['volCcy24h']) * price if tk and tk.get('volCcy24h') and price else None
        oi_usd = float(oi['oiCcy']) * price if oi and oi.get('oiCcy') and price else None
        out[('okx', inst)] = {'oi': oi_usd, 'vol': vol, 'price': price}
    return out


async def _hl_liquidity(client) -> dict:
    out: dict = {}
    try:
        r = await client.post(HL_URL, json={'type': 'metaAndAssetCtxs', 'dex': 'xyz'}, timeout=25.0)
        if r.status_code != 200:
            return out
        data = r.json()
        if not isinstance(data, list) or len(data) < 2:
            return out
        names = [u['name'] for u in data[0].get('universe', [])]
        ctxs = data[1]
        for i, name in enumerate(names):
            if i >= len(ctxs):
                break
            ctx = ctxs[i]
            mark = float(ctx['markPx']) if ctx.get('markPx') else None
            oi = float(ctx['openInterest']) * mark if ctx.get('openInterest') and mark else None
            vol = float(ctx['dayNtlVlm']) if ctx.get('dayNtlVlm') else None  # уже в $
            out[('hyperliquid', name)] = {'oi': oi, 'vol': vol, 'price': mark}
    except Exception as e:
        print(f'leaderboard HL liquidity err: {e}')
    return out


# ── Vantage: своп %/ночь + спред % (Блок 40) ───────────────────────────────
# Помечаем инструменты, которые есть на Vantage (хеджируемы второй ногой), и
# считаем норму свопа/спреда, чтобы фронт мог показать НЕТТО (фандинг минус
# издержки второй ноги). Формулы — те же, что в analysis.js (swapPctPerNight).

def _swap_night_pct(vs: dict, price: float | None) -> tuple[float | None, float | None]:
    """(swap_long, swap_short) в % от ноционала за ночь. POINTS требует цену
    инструмента (берём live-цену с крипто-биржи — металл ~= один и тот же
    уровень); годовой режим (акции) от цены не зависит (делим на 360)."""
    mode = vs.get('swap_mode')
    sl, ss, digits = vs.get('swap_long'), vs.get('swap_short'), vs.get('digits')
    if mode in SWAP_MODE_DISABLED:
        return 0.0, 0.0
    if mode in SWAP_MODE_POINTS:
        if not price or sl is None or ss is None or digits is None:
            return None, None
        point = 10.0 ** (-digits)
        return sl * point / price * 100, ss * point / price * 100
    if mode in SWAP_MODE_ANNUAL_PCT:
        if sl is None or ss is None:
            return None, None
        return sl / 360.0, ss / 360.0
    return None, None


def _build_vantage_matcher(vsyms: list[dict], asset_vantage: dict | None = None):
    """asset_vantage — авторитетная ручная привязка из дашборда {ключ_актива:
    тикер_Vantage} (assets.vantage). Имя на бирже и на Vantage часто не
    совпадает (GOOGL на биржах = 'GOOG' на Vantage; часть тикеров у брокера —
    полное название компании, напр. 'NVIDIA'/'AMAZON', см. Блок 33), поэтому
    сначала пробуем эту привязку, а уже потом эвристику (точное имя / +USD /
    алиасы металлов)."""
    vmap = {s['symbol'].upper(): s for s in vsyms}
    av = {k.upper(): (v or '').upper() for k, v in (asset_vantage or {}).items() if v}

    def match(base: str) -> dict | None:
        norm = base.upper()
        # 1) авторитетная ручная привязка из дашборда
        if norm in av and av[norm] in vmap:
            return vmap[av[norm]]
        # 2) эвристика: металлы → точное имя → +USD
        cands = []
        if norm in METAL_ALIASES:
            m = METAL_ALIASES[norm]
            cands += [m + 'USD', m]
        cands += [norm, norm + 'USD']
        for c in cands:
            if c in vmap:
                return vmap[c]
        return None

    return match


def _vantage_row(vs: dict, price: float | None) -> dict:
    ln, sn = _swap_night_pct(vs, price)
    spread = vs.get('spread')
    spread_pct = (spread / price * 100) if (spread is not None and price) else None
    return {
        'symbol': vs['symbol'],
        'swap_long_night': round(ln, 6) if ln is not None else None,
        'swap_short_night': round(sn, 6) if sn is not None else None,
        'spread_pct': round(spread_pct, 5) if spread_pct is not None else None,
    }


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

            # Ликвидность (Блок 40) — собираем один раз батчами до основного
            # прохода: OKX/HL отдают всё за 1-2 запроса, Binance OI — поштучно.
            liq: dict = {}
            try:
                b_syms = {i['symbol'] for i in binance}
                o_insts = {i['symbol'] for i in okx}
                b_liq, o_liq, h_liq = await asyncio.gather(
                    _binance_liquidity(client, b_syms),
                    _okx_liquidity(client, o_insts),
                    _hl_liquidity(client),
                )
                liq.update(b_liq); liq.update(o_liq); liq.update(h_liq)
            except Exception as e:
                print(f'leaderboard liquidity err: {e}')

            # Vantage-матчер (Блок 40) — из кэша vantage_symbols, для флага
            # «есть на Vantage» и расчёта нетто (своп/спред второй ноги). Плюс
            # ручная привязка из дашборда (assets.vantage) как авторитетный
            # источник, когда имя на бирже и на Vantage не совпадает (GOOGL→GOOG).
            try:
                assets = await get_all_assets()
                asset_vantage = {a['key']: a.get('vantage') for a in assets if a.get('vantage')}
                vantage_match = _build_vantage_matcher(await get_vantage_symbols(), asset_vantage)
            except Exception as e:
                print(f'leaderboard vantage load err: {e}')
                vantage_match = lambda base: None

            fresh = {}   # (exchange, symbol) -> строка, успешно собранная СЕЙЧАС

            async def work(item):
                try:
                    pts = await _fetch_funding(client, item, start_ms, now_ms)
                    d0, dv = _daily(pts)
                    # Кладём только строки, где реально собрались данные. Недобор
                    # (dv пустой) в этот раз пропускаем — закроет склейка ниже.
                    if not dv or not any(x is not None for x in dv):
                        return
                    key = (item['exchange'], item['symbol'])
                    lq = liq.get(key) or {}
                    vs = vantage_match(item['base'])
                    row = {
                        'exchange': item['exchange'], 'symbol': item['symbol'],
                        'base': item['base'], 'cls': item['cls'],
                        'd0': d0, 'dv': dv,
                        'oi': round(lq['oi']) if lq.get('oi') is not None else None,
                        'vol': round(lq['vol']) if lq.get('vol') is not None else None,
                        # Цена уже приходит из прохода по ликвидности (нужна была
                        # для POINTS-свопа Vantage) — сохраняем её в строку, чтобы
                        # экран «Спред между биржами» (Блок 41) мог показать
                        # текущее расхождение цен по паре бирж без своего сбора.
                        'price': lq.get('price'),
                        'updated_at': now_ms,
                    }
                    if vs:
                        row['vantage'] = _vantage_row(vs, lq.get('price'))
                    fresh[key] = row
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
        # посчитанные строки, не сырую историю фандинга. Строки старого формата
        # (без дневного ряда dv, Блок 39) отбрасываем — фронт их не отрисует.
        merged = {}
        prev = await get_leaderboard_cache('latest')
        if prev:
            for row in json.loads(prev['data']).get('rows', []):
                if 'dv' not in row:
                    continue
                if now_ms - row.get('updated_at', 0) <= STALE_KEEP_DAYS * 86400000:
                    merged[(row['exchange'], row['symbol'])] = row
        merged.update(fresh)   # свежие перекрывают старые
        rows = list(merged.values())

        payload = {'rows': rows, 'day_ms': DAY_MS,
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
