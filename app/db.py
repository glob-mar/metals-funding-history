import aiosqlite
from pathlib import Path

DB_PATH = Path('data/funding.db')

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
"""


async def init_db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
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
