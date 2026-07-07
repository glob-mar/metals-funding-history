ASSETS = {
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
        'binance': 'BZUSDT',          # запущен с апреля 2026
        'hyperliquid_dex': 'xyz',
        'hyperliquid_coin': 'xyz:BRENTOIL',
    },
    'NATGAS': {
        'label': 'Природный газ',
        'okx': None,
        'binance': 'NATGASUSDT',      # запущен с апреля 2026
        'hyperliquid_dex': 'xyz',
        'hyperliquid_coin': 'xyz:NATGAS',
    },
}

BINANCE_FAPI = 'https://fapi.binance.com/fapi/v1/fundingRate'
# Периодов в год по бирже
# Binance золото/серебро/платина/палладий: каждые 8ч (1095)
# Binance энергетика: каждые 8ч (1095)
PERIODS_PER_YEAR = {
    'hyperliquid': 8760,
    'okx':         1095,
    'binance':     1095,
}
