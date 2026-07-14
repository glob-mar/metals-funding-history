import os
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
"""


async def init_db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    print(f'DB: используется файл {DB_PATH.resolve()}')
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executescript(SCHEMA)
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
            SELECT key, label, okx, binance, hyperliquid_dex, hyperliquid_coin
            FROM assets ORDER BY id ASC
            """
        )
        rows = await cur.fetchall()
        return [dict(r) for r in rows]


async def insert_asset(key: str, label: str, okx: str | None, binance: str | None,
                        hyperliquid_dex: str | None, hyperliquid_coin: str | None) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            INSERT INTO assets (key, label, okx, binance, hyperliquid_dex, hyperliquid_coin)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (key, label, okx, binance, hyperliquid_dex, hyperliquid_coin)
        )
        await db.commit()


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
