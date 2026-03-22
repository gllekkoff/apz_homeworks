import asyncio
import os

import asyncpg
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

DATABASE_URL = os.getenv(
    "DATABASE_URL", "postgresql://banking_user:banking_pass@postgres:5432/banking"
)

app = FastAPI()

pool: asyncpg.Pool | None = None


async def connect_db(url: str, retries: int = 15, delay: float = 3.0) -> asyncpg.Pool:
    for attempt in range(retries):
        try:
            return await asyncpg.create_pool(url)
        except Exception as exc:
            if attempt < retries - 1:
                print(f"postgres not ready ({exc}), retry {attempt + 1}/{retries} in {delay}s")
                await asyncio.sleep(delay)
            else:
                raise


@app.on_event("startup")
async def startup():
    global pool
    pool = await connect_db(DATABASE_URL)
    async with pool.acquire() as conn:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS balances (
                user_id VARCHAR(255) PRIMARY KEY,
                balance NUMERIC(18, 4) NOT NULL DEFAULT 0
            )
        """)
    print("counter-service: connected to postgres")


@app.on_event("shutdown")
async def shutdown():
    await pool.close()


class UpdateRequest(BaseModel):
    user_id: str
    amount: float


@app.post("/update")
async def update_balance(req: UpdateRequest):
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO balances (user_id, balance)
            VALUES ($1, $2)
            ON CONFLICT (user_id) DO UPDATE
            SET balance = balances.balance + $2
            RETURNING balance
            """,
            req.user_id,
            req.amount,
        )
    balance = float(row["balance"])
    print(f"user={req.user_id} amount={req.amount:+} balance={balance}")
    return {"user_id": req.user_id, "balance": balance}


@app.get("/balance/{user_id}")
async def get_balance(user_id: str):
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT balance FROM balances WHERE user_id = $1", user_id
        )
    if not row:
        raise HTTPException(status_code=404, detail=f"user {user_id} not found")
    return {"user_id": user_id, "balance": float(row["balance"])}


@app.get("/balances")
async def get_all_balances():
    async with pool.acquire() as conn:
        rows = await conn.fetch("SELECT user_id, balance FROM balances ORDER BY user_id")
    return {"balances": {r["user_id"]: float(r["balance"]) for r in rows}}
