import os
import time
import aiosqlite
from pathlib import Path

# На Railway том смонтирован по пути из RAILWAY_VOLUME_MOUNT_PATH (переменную
# ставит сама платформа) — используем его напрямую, а не полагаемся на то,
# что относительный путь 'data/funding.db' совпадёт с точкой монтирования
# через текущую рабочую директорию процесса. Локально переменной нет —
# работаем как раньше, относительно cwd.
_volume_mount = os.environ.get('RAILWAY_VOLUME_MOUNT_PATH')
DB_PATH = Path(_volume_mount) / 'funding.db' if _volume_mount else Path('data/funding.db')

SCHEMA = """
CREATE TABLE IF NOT EXISTS funding_history (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    exchange    TEXT    NOT NULL,
    asset       TEXT    NOT NULL,
    symbol      TEXT    NOT NULL,
    funding_time INTEGER NOT NULL,
    funding_rate REAL   NOT NULL,
    mark_price  REAL,
    premium     REAL,
    UNIQUE(exchange, symbol, funding_time)
);
CREATE INDEX IF NOT EXISTS idx_asset_time
    ON funding_history(asset, funding_time);

CREATE TABLE IF NOT EXISTS price_history (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    exchange    TEXT    NOT NULL,
    asset       TEXT    NOT NULL,
    symbol      TEXT    NOT NULL,
    ts          INTEGER NOT NULL,
    open        REAL,
    high        REAL,
    low         REAL,
    close       REAL    NOT NULL,
    price_type  TEXT    NOT NULL,
    UNIQUE(exchange, symbol, ts, price_type)
);
CREATE INDEX IF NOT EXISTS idx_price_asset_time
    ON price_history(asset, ts);

CREATE TABLE IF NOT EXISTS assets (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    key              TEXT    NOT NULL UNIQUE,
    label            TEXT    NOT NULL,
    okx              TEXT,
    binance          TEXT,
    hyperliquid_dex  TEXT,
    hyperliquid_coin TEXT
);

CREATE TABLE IF NOT EXISTS vantage_symbols (
    symbol         TEXT    PRIMARY KEY,
    swap_long      REAL,
    swap_short     REAL,
    swap_mode      INTEGER,
    contract_size  REAL,
    margin_initial REAL,
    digits         INTEGER,
    updated_at     INTEGER NOT NULL
);
"""


async def init_db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    print(f'DB: используется файл {DB_PATH.resolve()}')
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executescript(SCHEMA)
        # ALTER TABLE ADD COLUMN — для БД, созданных до Блока 22, у которых
        # assets ещё нет колонки vantage (CREATE TABLE IF NOT EXISTS её не добавит).
        cur = await db.execute("PRAGMA table_info(assets)")
        cols = {row[1] for row in await cur.fetchall()}
        if 'vantage' not in cols:
            await db.execute("ALTER TABLE assets ADD COLUMN vantage TEXT")
        await db.commit()


async def seed_assets_if_empty(defaults: dict) -> None:
    """Заполняет таблицу assets дефолтным списком из config.py, только если
    она ещё пустая (первый запуск на новой БД/новом volume)."""
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute('SELECT COUNT(*) FROM assets')
        (count,) = await cur.fetchone()
        if count > 0:
            return
        await db.executemany(
            """
            INSERT INTO assets (key, label, okx, binance, hyperliquid_dex, hyperliquid_coin)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            [
                (key, info['label'], info.get('okx'), info.get('binance'),
                 info.get('hyperliquid_dex'), info.get('hyperliquid_coin'))
                for key, info in defaults.items()
            ]
        )
        await db.commit()


async def get_all_assets() -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            """
            SELECT key, label, okx, binance, hyperliquid_dex, hyperliquid_coin, vantage
            FROM assets ORDER BY id ASC
            """
        )
        rows = await cur.fetchall()
        return [dict(r) for r in rows]


async def insert_asset(key: str, label: str, okx: str | None, binance: str | None,
                        hyperliquid_dex: str | None, hyperliquid_coin: str | None,
                        vantage: str | None = None) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            INSERT INTO assets (key, label, okx, binance, hyperliquid_dex, hyperliquid_coin, vantage)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (key, label, okx, binance, hyperliquid_dex, hyperliquid_coin, vantage)
        )
        await db.commit()


async def upsert_vantage_symbols(rows: list[dict]) -> int:
    """Кэш списка инструментов счёта Vantage + их спецификация (Блок 22) —
    обновляется MQL5-скриптом по запуску, не в реальном времени. Источник
    для выпадающего списка в форме добавления актива (как instruments.py
    для остальных бирж, только пуллом с их API, а не пушем от терминала)."""
    if not rows:
        return 0
    sql = """
    INSERT INTO vantage_symbols (symbol, swap_long, swap_short, swap_mode, contract_size, margin_initial, digits, updated_at)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    ON CONFLICT(symbol) DO UPDATE SET
        swap_long = excluded.swap_long, swap_short = excluded.swap_short, swap_mode = excluded.swap_mode,
        contract_size = excluded.contract_size, margin_initial = excluded.margin_initial,
        digits = excluded.digits, updated_at = excluded.updated_at
    """
    now_ms = int(time.time() * 1000)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executemany(sql, [
            (r['symbol'], r.get('swap_long'), r.get('swap_short'), r.get('swap_mode'),
             r.get('contract_size'), r.get('margin_initial'), r.get('digits'), now_ms)
            for r in rows
        ])
        await db.commit()
        return db.total_changes


async def get_vantage_symbols() -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            """
            SELECT symbol, swap_long, swap_short, swap_mode, contract_size, margin_initial, digits, updated_at
            FROM vantage_symbols ORDER BY symbol ASC
            """
        )
        rows = await cur.fetchall()
        return [dict(r) for r in rows]


async def delete_asset(key: str) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute('DELETE FROM assets WHERE key = ?', (key,))
        await db.commit()
        return db.total_changes


async def upsert_rows(rows: list[dict]) -> int:
    if not rows:
        return 0
    sql = """
    INSERT OR IGNORE INTO funding_history
        (exchange, asset, symbol, funding_time, funding_rate, mark_price, premium)
    VALUES (?, ?, ?, ?, ?, ?, ?)
    """
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executemany(sql, [
            (r['exchange'], r['asset'], r['symbol'],
             r['funding_time'], r['funding_rate'],
             r.get('mark_price'), r.get('premium'))
            for r in rows
        ])
        await db.commit()
        return db.total_changes


async def get_history(asset: str) -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            """
            SELECT exchange, asset, symbol, funding_time,
                   funding_rate, mark_price, premium
            FROM funding_history
            WHERE asset = ?
            ORDER BY funding_time ASC, exchange ASC
            """,
            (asset,)
        )
        rows = await cur.fetchall()
        return [dict(r) for r in rows]


async def upsert_price_rows(rows: list[dict]) -> int:
    if not rows:
        return 0
    sql = """
    INSERT OR IGNORE INTO price_history
        (exchange, asset, symbol, ts, open, high, low, close, price_type)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executemany(sql, [
            (r['exchange'], r['asset'], r['symbol'], r['ts'],
             r.get('open'), r.get('high'), r.get('low'), r['close'], r['price_type'])
            for r in rows
        ])
        await db.commit()
        return db.total_changes


async def get_vantage_price_summary() -> list[dict]:
    """Диагностика (Блок 26): раскладка price_history по (asset, symbol) для
    exchange='vantage' в обход ограничения asset in ASSETS у /api/price-history —
    чтобы видеть вообще все символы, которые реально получили историю от бэкфилла,
    включая те ~993, для которых ещё нет актива в UI."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            """
            SELECT asset, symbol, COUNT(*) as cnt, MIN(ts) as min_ts, MAX(ts) as max_ts
            FROM price_history
            WHERE exchange = 'vantage'
            GROUP BY asset, symbol
            ORDER BY cnt DESC
            """
        )
        rows = await cur.fetchall()
        return [dict(r) for r in rows]


async def get_price_history(asset: str) -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            """
            SELECT exchange, asset, symbol, ts, open, high, low, close, price_type
            FROM price_history
            WHERE asset = ?
            ORDER BY ts ASC, exchange ASC
            """,
            (asset,)
        )
        rows = await cur.fetchall()
        return [dict(r) for r in rows]
