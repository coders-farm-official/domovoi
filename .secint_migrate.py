import asyncio, pathlib, sys, asyncpg

REPO = pathlib.Path(__file__).resolve().parent
MIGRATIONS = REPO / "domovoi" / "db" / "migrations"
BASE = "postgresql://domovoi:domovoi@127.0.0.1:6450"


async def connect(dsn, retries=90):
    last = None
    for _ in range(retries):
        try:
            return await asyncpg.connect(dsn)
        except Exception as e:  # noqa: BLE001
            last = e
            await asyncio.sleep(1)
    raise SystemExit(f"postgres never reachable at {dsn}: {last}")


async def main():
    files = sorted(MIGRATIONS.glob("V*.sql"))
    if not files:
        raise SystemExit(f"no migrations under {MIGRATIONS}")
    print("migrations:", [f.name for f in files])
    admin = await connect(BASE + "/postgres")
    try:
        for db in ("domovoi", "domovoi_test"):
            exists = await admin.fetchval(
                "SELECT 1 FROM pg_database WHERE datname=$1", db)
            if exists:
                await admin.execute(f'DROP DATABASE "{db}"')
            await admin.execute(f'CREATE DATABASE "{db}"')
    finally:
        await admin.close()
    for db in ("domovoi", "domovoi_test"):
        conn = await connect(f"{BASE}/{db}")
        try:
            for f in files:
                await conn.execute(f.read_text(encoding="utf-8"))
            n = await conn.fetchval(
                "SELECT count(*) FROM information_schema.tables "
                "WHERE table_schema='public'")
            print(db, "->", len(files), "migrations,", n, "tables")
        finally:
            await conn.close()


asyncio.run(main())
