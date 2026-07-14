import asyncio
import os
from datetime import datetime, timezone
from .config import ASSETS
from .services import collect, collect_prices
from .db import upsert_rows, upsert_price_rows

# Интервал автосбора в минутах. Дефолт 60 — совпадает с часовым фандингом
# Hyperliquid и укладывается в интервалы 4ч/6ч/8ч остальных активов с запасом.
AUTO_SYNC_MINUTES = int(os.environ.get('AUTO_SYNC_MINUTES', '60'))
# Задержка перед самым первым проходом (даём приложению стартовать) —
# вынесена в env var, чтобы можно было ускорить в тестах, не трогая код.
INITIAL_DELAY_SECONDS = int(os.environ.get('AUTO_SYNC_INITIAL_DELAY_SECONDS', '30'))
# Пауза между активами внутри одного прохода, чтобы не долбить биржи пачкой
# запросов подряд без пауз (ручной клик по одной карточке это делает
# естественным образом за счёт человека, а автопроход — нет).
ASSET_DELAY_SECONDS = 2

status = {
    'enabled': True,
    'interval_minutes': AUTO_SYNC_MINUTES,
    'started_at': None,
    'finished_at': None,
    'running': False,
    'results': {},
}


def _now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


async def sync_all_assets() -> dict:
    status['running'] = True
    status['started_at'] = _now_iso()
    results = {}
    for asset in list(ASSETS.keys()):
        try:
            rows = await collect(asset)
            inserted = await upsert_rows(rows)
            price_rows = await collect_prices(asset)
            price_inserted = await upsert_price_rows(price_rows)
            results[asset] = {
                'ok': True,
                'received': len(rows), 'new': inserted,
                'price_received': len(price_rows), 'price_new': price_inserted,
            }
            print(f'Автосбор {asset}: +{inserted} фандинг, +{price_inserted} цены')
        except Exception as e:
            results[asset] = {'ok': False, 'error': f'{type(e).__name__}: {e}'}
            print(f'Автосбор {asset}: ОШИБКА {type(e).__name__}: {e}')
        await asyncio.sleep(ASSET_DELAY_SECONDS)
    status['results'] = results
    status['finished_at'] = _now_iso()
    status['running'] = False
    return results


async def scheduler_loop():
    # Даём приложению полностью стартовать (init_db/seed) перед первым проходом.
    await asyncio.sleep(INITIAL_DELAY_SECONDS)
    while True:
        try:
            await sync_all_assets()
        except Exception as e:
            print(f'Автосбор: неожиданная ошибка цикла: {type(e).__name__}: {e}')
        await asyncio.sleep(AUTO_SYNC_MINUTES * 60)
