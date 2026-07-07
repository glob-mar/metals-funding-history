from datetime import datetime, timezone
from io import BytesIO
import zipfile
import csv
import httpx
from .config import ASSETS, BINANCE_VISION_BASE

OKX_URL     = 'https://www.okx.com/api/v5/public/funding-rate-history'
BINANCE_API = 'https://fapi.binance.com/fapi/v1/fundingRate'


def _year_start_ms() -> int:
    return int(datetime(datetime.now().year, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)


def _now_ms() -> int:
    return int(datetime.now(tz=timezone.utc).timestamp() * 1000)


def _months_before_current():
    """Завершённые месяцы этого года — они точно есть в архиве Binance Vision."""
    now = datetime.now(tz=timezone.utc)
    return [(now.year, m) for m in range(1, now.month)]


async def _binance_vision(client: httpx.AsyncClient, asset: str) -> list[dict]:
    """Исторические данные из публичного архива data.binance.vision (ZIP/CSV)."""
    symbol = ASSETS[asset]['binance']
    rows = []
    year_start = _year_start_ms()

    for year, month in _months_before_current():
        month_str = f'{month:02d}'
        fname = f'{symbol}-fundingRate-{year}-{month_str}.zip'
        url = f'{BINANCE_VISION_BASE}/{symbol}/{fname}'
        try:
            r = await client.get(url, timeout=30.0)
            if r.status_code != 200:
                print(f'Binance Vision {symbol} {year}-{month_str}: HTTP {r.status_code}')
                continue
            with zipfile.ZipFile(BytesIO(r.content)) as z:
                with z.open(z.namelist()[0]) as f:
                    lines = f.read().decode('utf-8').splitlines()
            reader = csv.reader(lines)
            next(reader, None)
            for line in reader:
                if len(line) < 3:
                    continue
                try:
                    ft = int(line[1])
                except (ValueError, IndexError):
                    continue
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
            print(f'Binance Vision {symbol} {year}-{month_str}: OK +{len(rows)} rows')
        except Exception as e:
            print(f'Binance Vision {symbol} {year}-{month_str}: {e}')
    return rows


async def _binance_current_month(client: httpx.AsyncClient, asset: str) -> list[dict]:
    """
    Текущий месяц — архив ещё не готов, берём через fapi.
    Railway может блокировать 451, поэтому оборачиваем в try/except.
    """
    symbol = ASSETS[asset]['binance']
    rows = []
    now = datetime.now(tz=timezone.utc)
    month_start = int(datetime(now.year, now.month, 1, tzinfo=timezone.utc).timestamp() * 1000)
    end = _now_ms()
    after = None
    try:
        while True:
            params = {'symbol': symbol, 'startTime': month_start, 'endTime': end, 'limit': 1000}
            if after:
                params['startTime'] = after
            r = await client.get(BINANCE_API, params=params, timeout=20.0)
            if r.status_code in (451, 403):
                print(f'Binance API geo-blocked ({r.status_code}) for {symbol}, skipping current month')
                break
            r.raise_for_status()
            data = r.json()
            if not data:
                break
            for d in data:
                ft = int(d['fundingTime'])
                if ft < month_start:
                    continue
                rows.append({
                    'exchange': 'binance',
                    'asset': asset,
                    'symbol': symbol,
                    'funding_time': ft,
                    'funding_rate': float(d['fundingRate']),
                    'mark_price': float(d['markPrice']) if d.get('markPrice') else None,
                    'premium': None,
                })
            if len(data) < 1000:
                break
            after = data[-1]['fundingTime'] + 1
        print(f'Binance current month {symbol}: {len(rows)} rows')
    except Exception as e:
        print(f'Binance current month {symbol}: {e}')
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


async def collect(asset: str) -> list[dict]:
    timeout = httpx.Timeout(30.0)
    headers = {'User-Agent': 'metals-funding-history/1.0'}
    async with httpx.AsyncClient(timeout=timeout, headers=headers) as c:
        rows = []
        # Binance: архив прошлых месяцев
        try:
            rows += await _binance_vision(c, asset)
        except Exception as e:
            print(f'Binance Vision error ({asset}): {e}')
        # Binance: текущий месяц через API (может быть заблокирован)
        try:
            rows += await _binance_current_month(c, asset)
        except Exception as e:
            print(f'Binance current month error ({asset}): {e}')
        # OKX
        try:
            rows += await _okx(c, asset)
        except Exception as e:
            print(f'OKX error ({asset}): {e}')
        return rows
