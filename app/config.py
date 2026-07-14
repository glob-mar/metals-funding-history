# Дефолтный набор активов — используется только для заполнения таблицы assets
# в БД при самом первом запуске (на пустой БД/новом Railway volume). После
# этого единственный источник правды — таблица assets, редактируемая через UI
# (см. Блок 7). ASSETS ниже — общий изменяемый словарь: main.py/services.py
# держат ссылку на этот же объект, и load_assets_into() обновляет его на
# месте (не пересоздаёт), поэтому обновления видны везде без рестарта.
DEFAULT_ASSETS = {
    'XAU': {
        'label': 'Золото (Gold)',
        'okx': 'XAU-USDT-SWAP',
        'binance': 'XAUUSDT',
        'hyperliquid_dex': 'xyz',
        'hyperliquid_coin': 'xyz:GOLD',
    },
    'XAG': {
        'label': 'Серебро (Silver)',
        'okx': 'XAG-USDT-SWAP',
        'binance': 'XAGUSDT',
        'hyperliquid_dex': 'xyz',
        'hyperliquid_coin': 'xyz:SILVER',
    },
    'XPT': {
        'label': 'Платина (Platinum)',
        'okx': 'XPT-USDT-SWAP',
        'binance': 'XPTUSDT',
        'hyperliquid_dex': 'xyz',
        'hyperliquid_coin': 'xyz:PLATINUM',
    },
    'XPD': {
        'label': 'Палладий (Palladium)',
        'okx': 'XPD-USDT-SWAP',
        'binance': 'XPDUSDT',
        'hyperliquid_dex': 'xyz',
        'hyperliquid_coin': 'xyz:PALLADIUM',
    },
    'BRENT': {
        'label': 'Нефть Brent',
        'okx': 'BZ-USDT-SWAP',
        'binance': 'BZUSDT',
        'hyperliquid_dex': 'xyz',
        'hyperliquid_coin': 'xyz:BRENTOIL',
    },
    'NATGAS': {
        'label': 'Природный газ',
        'okx': None,
        'binance': 'NATGASUSDT',
        'hyperliquid_dex': 'xyz',
        'hyperliquid_coin': 'xyz:NATGAS',
    },
    'BTC': {
        'label': 'Bitcoin (BTC)',
        'okx': 'BTC-USDT-SWAP',
        'binance': 'BTCUSDT',
        # 'HyperEVM' здесь — placeholder-маркер "нативный dex, dex-параметр в
        # запросах не передаём" (см. _live_hyperliquid/_hyperliquid в services.py).
        # Сам coin для нативного dex — голое имя БЕЗ префикса (в отличие от
        # HIP-3 dex вроде 'xyz', где имена в universe идут как 'xyz:GOLD' и т.п.).
        # Раньше здесь стояло 'HyperEVM:BTC' — такого имени в universe нет,
        # из-за чего фандинг BTC на Hyperliquid не собирался (fundingHistory
        # с этим coin возвращает null). Обнаружено и исправлено в Блоке 7
        # при тестировании валидации тикеров.
        'hyperliquid_dex': 'HyperEVM',
        'hyperliquid_coin': 'BTC',
    },
}

# Общий изменяемый словарь активов. Пустой при импорте — main.py заполняет
# его строками из БД при старте (см. lifespan в main.py) через load_assets_into().
ASSETS: dict = {}


def load_assets_into(target: dict, rows: list[dict]) -> None:
    """Обновляет target (словарь ASSETS) на месте по строкам из БД, не
    пересоздавая объект — важно, т.к. другие модули держат ссылку на этот
    же словарь (from .config import ASSETS)."""
    target.clear()
    for row in rows:
        target[row['key']] = {
            'label': row['label'],
            'okx': row['okx'],
            'binance': row['binance'],
            'hyperliquid_dex': row['hyperliquid_dex'],
            'hyperliquid_coin': row['hyperliquid_coin'],
        }


BINANCE_FAPI = 'https://fapi.binance.com/fapi/v1/fundingRate'
