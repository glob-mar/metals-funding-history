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
"""


async def init_db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    print(f'DB: используется файл {DB_PATH.resolve()}')
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executescript(SCHEMA)
        await db.commit()


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
