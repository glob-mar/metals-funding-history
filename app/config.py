# Маппинг тикеров по биржам
# Binance: data.binance.vision — публичный архив, не блокируется
# OKX: публичный API работает
# Hyperliquid: металлы торгуются как HIP-3, тикер с префиксом @
ASSETS = {
    'XAU': {
        'label': 'Золото (Gold)',
        'binance': 'XAUUSDT',
        'okx': 'XAU-USDT-SWAP',
        'hyperliquid': '@7',   # XAU на Hyperliquid HIP-3
    },
    'XAG': {
        'label': 'Серебро (Silver)',
        'binance': 'XAGUSDT',
        'okx': 'XAG-USDT-SWAP',
        'hyperliquid': '@8',
    },
    'XPT': {
        'label': 'Платина (Platinum)',
        'binance': 'XPTUSDT',
        'okx': 'XPT-USDT-SWAP',
        'hyperliquid': '@9',
    },
    'XPD': {
        'label': 'Палладий (Palladium)',
        'binance': 'XPDUSDT',
        'okx': 'XPD-USDT-SWAP',
        'hyperliquid': '@10',
    },
}

# Binance Vision — публичный S3 архив с funding rate данными
# Не имеет гео-блокировок в отличие от fapi.binance.com
BINANCE_VISION_BASE = 'https://data.binance.vision/data/futures/um/monthly/fundingRate'
