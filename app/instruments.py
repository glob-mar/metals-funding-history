"""Проксирующие списки доступных инструментов по каждой бирже — для
выпадающих списков в форме добавления актива (Блок 14), вместо ручного
ввода тикеров. Списки большие (сотни штук) и почти не меняются в течение
дня, поэтому кэшируются в памяти процесса на TTL_SECONDS."""
import time
import httpx

TTL_SECONDS = 600
_cache: dict = {}

OKX_INSTRUMENTS_URL = 'https://www.okx.com/api/v5/public/instruments'
BINANCE_EXCHANGE_INFO_URL = 'https://fapi.binance.com/fapi/v1/exchangeInfo'
HL_URL = 'https://api.hyperliquid.xyz/info'

# Маркер нативного (не HIP-3) perp-dex на Hyperliquid — используется как
# значение dex везде в проекте (см. config.py, services.py): при реальных
# запросах к API параметр dex для него нужно ОПУСКАТЬ, а не передавать буквально.
NATIVE_DEX = 'HyperEVM'


async def _cached(key: str, fetch_fn):
    now = time.monotonic()
    entry = _cache.get(key)
    if entry and now - entry[0] < TTL_SECONDS:
        return entry[1]
    data = await fetch_fn()
    _cache[key] = (now, data)
    return data


async def list_okx_instruments() -> list[str]:
    async def fetch():
        async with httpx.AsyncClient(timeout=15.0) as c:
            r = await c.get(OKX_INSTRUMENTS_URL, params={'instType': 'SWAP'})
            data = r.json().get('data', [])
        items = [
            d['instId'] for d in data
            if d.get('state') == 'live' and d.get('ctType') == 'linear' and d['instId'].endswith('-USDT-SWAP')
        ]
        return sorted(items)
    return await _cached('okx', fetch)


async def list_binance_instruments() -> list[str]:
    async def fetch():
        async with httpx.AsyncClient(timeout=15.0) as c:
            r = await c.get(BINANCE_EXCHANGE_INFO_URL)
            data = r.json().get('symbols', [])
        items = [
            d['symbol'] for d in data
            if d.get('contractType') == 'PERPETUAL' and d.get('status') == 'TRADING' and d['symbol'].endswith('USDT')
        ]
        return sorted(items)
    return await _cached('binance', fetch)


async def list_hyperliquid_dexes() -> list[dict]:
    async def fetch():
        async with httpx.AsyncClient(timeout=15.0) as c:
            r = await c.post(HL_URL, json={'type': 'perpDexs'})
            data = r.json()
        result = [{'value': NATIVE_DEX, 'label': 'Нативный (HyperEVM)'}]
        for d in data:
            if d is None:
                continue
            result.append({'value': d['name'], 'label': f"{d['name']} ({d['fullName']})"})
        return result
    return await _cached('hyperliquid_dexes', fetch)


async def list_hyperliquid_coins(dex: str) -> list[str]:
    async def fetch():
        payload = {'type': 'metaAndAssetCtxs'}
        if dex != NATIVE_DEX:
            payload['dex'] = dex
        async with httpx.AsyncClient(timeout=15.0) as c:
            r = await c.post(HL_URL, json=payload)
            data = r.json()
        if not isinstance(data, list):
            return []
        return sorted(u['name'] for u in data[0]['universe'])
    return await _cached(f'hyperliquid_coins:{dex}', fetch)
