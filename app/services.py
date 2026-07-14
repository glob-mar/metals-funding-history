from datetime import datetime, timezone
import httpx
from .config import ASSETS, BINANCE_FAPI

OKX_URL = 'https://www.okx.com/api/v5/public/funding-rate-history'
HL_URL  = 'https://api.hyperliquid.xyz/info'
HL_CHUNK_MS = 7 * 24 * 3600 * 1000  # 7 дней в мс

BINANCE_MARK_KLINES = 'https://fapi.binance.com/fapi/v1/markPriceKlines'
OKX_MARK_CANDLES_URL = 'https://www.okx.com/api/v5/market/history-mark-price-candles'

# Нефть и газ волатильнее и торгуются с более коротким шагом фандинга (4ч) —
# для них берём 4-часовые свечи цены, для остальных активов — 8-часовые.
# OKX не поддерживает bar=8H (проверено: "Parameter bar error"), ближайший
# доступный шаг — 6H.
FAST_INTERVAL_ASSETS = {'BRENT', 'NATGAS'}
PRICE_INTERVALS = {
    'default': {'binance': '8h', 'okx': '6H', 'hyperliquid': '8h'},
    'fast':    {'binance': '4h', 'okx': '4H', 'hyperliquid': '4h'},
}


def _price_interval(asset: str, exchange: str) -> str:
    profile = 'fast' if asset in FAST_INTERVAL_ASSETS else 'default'
    return PRICE_INTERVALS[profile][exchange]


def _year_start_ms() -> int:
    return int(datetime(datetime.now().year, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)


def _now_ms() -> int:
    return int(datetime.now(tz=timezone.utc).timestamp() * 1000)


async def _okx(client: httpx.AsyncClient, asset: str) -> list[dict]:
    inst = ASSETS[asset].get('okx')
    if not inst:
        return []
    rows = []
    limit_ts = _year_start_ms()
    after = None
    page = 0
    while True:
        params = {'instId': inst, 'limit': 100}
        if after:
            params['after'] = str(after)
        r = await client.get(OKX_URL, params=params, timeout=20.0)
        r.raise_for_status()
        data = r.json().get('data', [])
        if not data:
            break
        page += 1
        stop = False
        for d in data:
            ft = int(d['fundingTime'])
            if ft < limit_ts:
                stop = True
                break
            rows.append({
                'exchange': 'okx',
                'asset': asset,
                'symbol': inst,
                'funding_time': ft,
                'funding_rate': float(d['fundingRate']),
                'mark_price': None,
                'premium': float(d['realizedRate']) if d.get('realizedRate') not in (None, '') else None,
            })
        print(f'OKX {inst} page {page}: {len(data)} records, total: {len(rows)}')
        if stop or len(data) < 100:
            break
        after = min(int(d['fundingTime']) for d in data)
    rows.sort(key=lambda x: x['funding_time'])
    return rows


async def _binance(client: httpx.AsyncClient, asset: str) -> list[dict]:
    """
    Binance USDM fapi/v1/fundingRate.
    Пагинация через startTime: берём последний fundingTime + 1 и повторяем.
    Лимит 1000 записей за запрос.
    """
    symbol = ASSETS[asset].get('binance')
    if not symbol:
        return []
    rows = []
    start = _year_start_ms()
    end   = _now_ms()
    page  = 0
    while start < end:
        params = {
            'symbol':    symbol,
            'startTime': start,
            'endTime':   end,
            'limit':     1000,
        }
        r = await client.get(BINANCE_FAPI, params=params, timeout=20.0)
        if r.status_code != 200:
            print(f'Binance {symbol}: HTTP {r.status_code} {r.text[:200]}')
            break
        data = r.json()
        if not isinstance(data, list) or not data:
            break
        page += 1
        for d in data:
            rows.append({
                'exchange':    'binance',
                'asset':       asset,
                'symbol':      symbol,
                'funding_time': int(d['fundingTime']),
                'funding_rate': float(d['fundingRate']),
                'mark_price':   float(d['markPrice']) if d.get('markPrice') not in (None, '', '0') else None,
                'premium':      None,
            })
        print(f'Binance {symbol} page {page}: +{len(data)}, total: {len(rows)}')
        if len(data) < 1000:
            break
        # следующая страница: с следующей миллисекунды после последней записи
        start = int(data[-1]['fundingTime']) + 1
    # дедупликация
    seen = set()
    unique = []
    for r in rows:
        key = (r['exchange'], r['symbol'], r['funding_time'])
        if key not in seen:
            seen.add(key)
            unique.append(r)
    unique.sort(key=lambda x: x['funding_time'])
    print(f'Binance {symbol}: DONE, {len(unique)} rows')
    return unique


async def _hyperliquid(client: httpx.AsyncClient, asset: str) -> list[dict]:
    dex  = ASSETS[asset]['hyperliquid_dex']
    coin = ASSETS[asset]['hyperliquid_coin']
    rows = []
    seen = set()
    start = _year_start_ms()
    end   = _now_ms()
    chunk_start = start
    chunk_num = 0
    while chunk_start < end:
        chunk_end = min(chunk_start + HL_CHUNK_MS, end)
        chunk_num += 1
        try:
            r = await client.post(HL_URL,
                json={'type': 'fundingHistory', 'coin': coin, 'dex': dex,
                      'startTime': chunk_start, 'endTime': chunk_end},
                timeout=20.0)
            if r.status_code != 200:
                chunk_start = chunk_end + 1
                continue
            data = r.json()
            if not isinstance(data, list):
                chunk_start = chunk_end + 1
                continue
            new_in_chunk = 0
            for d in data:
                key = int(d['time'])
                if key in seen:
                    continue
                seen.add(key)
                new_in_chunk += 1
                rows.append({
                    'exchange': 'hyperliquid',
                    'asset': asset,
                    'symbol': coin,
                    'funding_time': key,
                    'funding_rate': float(d['fundingRate']),
                    'mark_price': None,
                    'premium': float(d['premium']) if d.get('premium') not in (None, '') else None,
                })
            print(f'HL {coin} chunk {chunk_num}: +{new_in_chunk}, total: {len(rows)}')
        except Exception as e:
            print(f'HL {coin} chunk {chunk_num}: {e}')
        chunk_start = chunk_end + 1
    rows.sort(key=lambda x: x['funding_time'])
    print(f'HL {coin}: DONE, {len(rows)} rows')
    return rows


async def _binance_price(client: httpx.AsyncClient, asset: str) -> list[dict]:
    """Binance markPriceKlines — свечи mark price (8ч/4ч по активу), лимит 1500/запрос."""
    symbol = ASSETS[asset].get('binance')
    if not symbol:
        return []
    rows = []
    start = _year_start_ms()
    end = _now_ms()
    page = 0
    while start < end:
        params = {
            'symbol':    symbol,
            'interval':  _price_interval(asset, 'binance'),
            'startTime': start,
            'endTime':   end,
            'limit':     1500,
        }
        r = await client.get(BINANCE_MARK_KLINES, params=params, timeout=20.0)
        if r.status_code != 200:
            print(f'Binance price {symbol}: HTTP {r.status_code} {r.text[:200]}')
            break
        data = r.json()
        if not isinstance(data, list) or not data:
            break
        page += 1
        for d in data:
            rows.append({
                'exchange': 'binance', 'asset': asset, 'symbol': symbol,
                'ts': int(d[0]), 'open': float(d[1]), 'high': float(d[2]),
                'low': float(d[3]), 'close': float(d[4]), 'price_type': 'mark',
            })
        print(f'Binance price {symbol} page {page}: +{len(data)}, total: {len(rows)}')
        if len(data) < 1500:
            break
        start = int(data[-1][0]) + 1
    seen = set()
    unique = []
    for r in rows:
        key = (r['exchange'], r['symbol'], r['ts'])
        if key not in seen:
            seen.add(key)
            unique.append(r)
    unique.sort(key=lambda x: x['ts'])
    return unique


async def _okx_price(client: httpx.AsyncClient, asset: str) -> list[dict]:
    """OKX history-mark-price-candles — свечи mark price (6ч/4ч по активу), лимит 100/запрос."""
    inst = ASSETS[asset].get('okx')
    if not inst:
        return []
    rows = []
    limit_ts = _year_start_ms()
    after = None
    page = 0
    while True:
        params = {'instId': inst, 'bar': _price_interval(asset, 'okx'), 'limit': 100}
        if after:
            params['after'] = str(after)
        r = await client.get(OKX_MARK_CANDLES_URL, params=params, timeout=20.0)
        r.raise_for_status()
        data = r.json().get('data', [])
        if not data:
            break
        page += 1
        stop = False
        for d in data:
            ts = int(d[0])
            if ts < limit_ts:
                stop = True
                break
            rows.append({
                'exchange': 'okx', 'asset': asset, 'symbol': inst,
                'ts': ts, 'open': float(d[1]), 'high': float(d[2]),
                'low': float(d[3]), 'close': float(d[4]), 'price_type': 'mark',
            })
        print(f'OKX price {inst} page {page}: {len(data)} records, total: {len(rows)}')
        if stop or len(data) < 100:
            break
        after = min(int(d[0]) for d in data)
    rows.sort(key=lambda x: x['ts'])
    return rows


async def _hyperliquid_price(client: httpx.AsyncClient, asset: str) -> list[dict]:
    """Hyperliquid candleSnapshot — до 5000 свечей за запрос, для 8ч/4ч свечей за год хватает
    одного вызова. Возвращает цену исполнения (не отдельный mark-price ряд — HL не отдаёт
    исторический mark отдельным эндпоинтом), помечаем price_type='mark' для единообразия
    с другими биржами."""
    coin = ASSETS[asset]['hyperliquid_coin']
    start = _year_start_ms()
    end = _now_ms()
    rows = []
    try:
        r = await client.post(HL_URL,
            json={'type': 'candleSnapshot',
                  'req': {'coin': coin, 'interval': _price_interval(asset, 'hyperliquid'),
                          'startTime': start, 'endTime': end}},
            timeout=20.0)
        if r.status_code != 200:
            print(f'HL price {coin}: HTTP {r.status_code}')
            return []
        data = r.json()
        if not isinstance(data, list):
            return []
        for d in data:
            rows.append({
                'exchange': 'hyperliquid', 'asset': asset, 'symbol': coin,
                'ts': int(d['t']), 'open': float(d['o']), 'high': float(d['h']),
                'low': float(d['l']), 'close': float(d['c']), 'price_type': 'mark',
            })
    except Exception as e:
        print(f'HL price {coin}: {e}')
        return []
    rows.sort(key=lambda x: x['ts'])
    print(f'HL price {coin}: DONE, {len(rows)} rows')
    return rows


async def collect_prices(asset: str) -> list[dict]:
    timeout = httpx.Timeout(120.0)
    headers = {'User-Agent': 'metals-funding-history/1.0'}
    async with httpx.AsyncClient(timeout=timeout, headers=headers) as c:
        rows = []
        try:
            rows += await _binance_price(c, asset)
        except Exception as e:
            print(f'Binance price error ({asset}): {type(e).__name__}: {e}')
        try:
            rows += await _okx_price(c, asset)
        except Exception as e:
            print(f'OKX price error ({asset}): {type(e).__name__}: {e}')
        try:
            rows += await _hyperliquid_price(c, asset)
        except Exception as e:
            print(f'Hyperliquid price error ({asset}): {type(e).__name__}: {e}')
        return rows


async def collect(asset: str) -> list[dict]:
    timeout = httpx.Timeout(120.0)
    headers = {'User-Agent': 'metals-funding-history/1.0'}
    async with httpx.AsyncClient(timeout=timeout, headers=headers) as c:
        rows = []
        try:
            rows += await _okx(c, asset)
        except Exception as e:
            print(f'OKX error ({asset}): {e}')
        try:
            rows += await _binance(c, asset)
        except Exception as e:
            print(f'Binance error ({asset}): {e}')
        try:
            rows += await _hyperliquid(c, asset)
        except Exception as e:
            print(f'Hyperliquid error ({asset}): {e}')
        return rows
