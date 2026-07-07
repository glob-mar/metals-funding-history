from datetime import datetime, timezone
from io import BytesIO
import zipfile
import csv
import httpx
from .config import ASSETS, BINANCE_VISION_BASE

OKX_URL = 'https://www.okx.com/api/v5/public/funding-rate-history'
HL_URL  = 'https://api.hyperliquid.xyz/info'


def _year_start_ms() -> int:
    return int(datetime(datetime.now().year, 1, 1,
                        tzinfo=timezone.utc).timestamp() * 1000)


def _now_ms() -> int:
    return int(datetime.now(tz=timezone.utc).timestamp() * 1000)


def _months_this_year():
    """Возвращает список (year, month) от января до текущего месяца включительно."""
    now = datetime.now(tz=timezone.utc)
    months = []
    for m in range(1, now.month + 1):
        months.append((now.year, m))
    return months


async def _binance_vision(client: httpx.AsyncClient, asset: str) -> list[dict]:
    """
    Скачивает данные с data.binance.vision — публичный архив Binance.
    Файлы: ZIP с CSV внутри.
    URL пример: https://data.binance.vision/data/futures/um/monthly/fundingRate/XAUUSDT/XAUUSDT-fundingRate-2026-01.zip
    """
    symbol = ASSETS[asset]['binance']
    rows = []
    year_start = _year_start_ms()

    for year, month in _months_this_year():
        month_str = f'{month:02d}'
        fname = f'{symbol}-fundingRate-{year}-{month_str}.zip'
        url = f'{BINANCE_VISION_BASE}/{symbol}/{fname}'
        try:
            r = await client.get(url, timeout=30.0)
            if r.status_code == 404:
                continue
            r.raise_for_status()
            with zipfile.ZipFile(BytesIO(r.content)) as z:
                csv_name = z.namelist()[0]
                with z.open(csv_name) as f:
                    reader = csv.reader(line.decode() for line in f)
                    next(reader, None)  # пропустить заголовок
                    for line in reader:
                        if len(line) < 3:
                            continue
                        ft = int(line[1])
                        if ft < year_start:
                            continue
                        rows.append({
                            'exchange': 'binance',
                            'asset': asset,
                            'symbol': symbol,
                            'funding_time': ft,
                            'funding_rate': float(line[2]),
                            'mark_price': None,
                            'premium': None,
                        })
        except Exception as e:
            print(f'Binance Vision {symbol} {year}-{month_str}: {e}')
            continue

    return rows


async def _okx(client: httpx.AsyncClient, asset: str) -> list[dict]:
    inst = ASSETS[asset]['okx']
    after = None
    rows = []
    limit_ts = _year_start_ms()
    while True:
        params = {'instId': inst, 'limit': 100}
        if after:
            params['after'] = after
        r = await client.get(OKX_URL, params=params, timeout=20.0)
        r.raise_for_status()
        data = r.json().get('data', [])
        if not data:
            break
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
        if stop or len(data) < 100:
            break
        after = data[-1]['fundingTime']
    rows.sort(key=lambda x: x['funding_time'])
    return rows


async def _hyperliquid(client: httpx.AsyncClient, asset: str) -> list[dict]:
    """
    Пробуем сначала базовый тикер (XAU),
    если 500/пусто — пробуем HIP-3 индекс (@7, @8 и т.д. из config).
    """
    coin_base  = asset           # 'XAU'
    coin_hip3  = ASSETS[asset]['hyperliquid']  # '@7'
    start      = _year_start_ms()
    end        = _now_ms()

    rows = []
    for coin in [coin_base, coin_hip3]:
        try:
            r = await client.post(HL_URL, json={
                'type': 'fundingHistory',
                'coin': coin,
                'startTime': start,
                'endTime': end,
            }, timeout=20.0)
            if r.status_code != 200:
                continue
            data = r.json()
            if not data:
                continue
            for d in data:
                rows.append({
                    'exchange': 'hyperliquid',
                    'asset': asset,
                    'symbol': coin,
                    'funding_time': int(d['time']),
                    'funding_rate': float(d['fundingRate']),
                    'mark_price': None,
                    'premium': float(d['premium']) if d.get('premium') not in (None, '') else None,
                })
            if rows:
                break  # нашли данные — дальше не пробуем
        except Exception as e:
            print(f'Hyperliquid {coin}: {e}')
            continue

    return rows


async def collect(asset: str) -> list[dict]:
    timeout = httpx.Timeout(30.0)
    headers = {'User-Agent': 'metals-funding-history/1.0'}
    async with httpx.AsyncClient(timeout=timeout, headers=headers) as c:
        rows = []

        # Binance через публичный архив (не блокируется)
        try:
            rows += await _binance_vision(c, asset)
        except Exception as e:
            print(f'Binance error ({asset}): {e}')

        # OKX
        try:
            rows += await _okx(c, asset)
        except Exception as e:
            print(f'OKX error ({asset}): {e}')

        # Hyperliquid
        try:
            rows += await _hyperliquid(c, asset)
        except Exception as e:
            print(f'Hyperliquid error ({asset}): {e}')

        return rows
