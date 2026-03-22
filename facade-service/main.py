import asyncio
import logging
import os
import random
import time
import uuid
from datetime import datetime, timezone

import httpx
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

app = FastAPI()

logger = logging.getLogger("uvicorn.error")

LOGGING_URLS: list[str] = [
    u.strip()
    for u in os.getenv(
        "LOGGING_SERVICE_URLS",
        "http://logging-service1:8081,http://logging-service2:8081,http://logging-service3:8081",
    ).split(",")
    if u.strip()
]
COUNTER_URL = os.getenv("COUNTER_SERVICE_URL", "http://counter-service:8082")

logging_total_time: float = 0.0
counter_total_time: float = 0.0


class TransactionRequest(BaseModel):
    user_id: str
    amount: float


async def log_post(client: httpx.AsyncClient, payload: dict) -> tuple[dict, float]:
    urls = random.sample(LOGGING_URLS, len(LOGGING_URLS))
    t0 = time.perf_counter()
    last_exc = None
    for url in urls:
        try:
            resp = await client.post(f"{url}/log", json=payload)
            resp.raise_for_status()
            return resp.json(), time.perf_counter() - t0
        except Exception as exc:
            last_exc = exc
            logger.warning("logging %s unreachable: %s", url, exc)
    raise HTTPException(503, detail=str(last_exc))


async def log_get(client: httpx.AsyncClient, path: str) -> tuple[dict, float]:
    urls = random.sample(LOGGING_URLS, len(LOGGING_URLS))
    t0 = time.perf_counter()
    last_exc = None
    for url in urls:
        try:
            resp = await client.get(f"{url}{path}")
            resp.raise_for_status()
            return resp.json(), time.perf_counter() - t0
        except Exception as exc:
            last_exc = exc
            logger.warning("logging %s unreachable: %s", url, exc)
    raise HTTPException(503, detail=str(last_exc))


async def counter_update(client: httpx.AsyncClient, user_id: str, amount: float) -> tuple[dict, float]:
    t0 = time.perf_counter()
    resp = await client.post(f"{COUNTER_URL}/update", json={"user_id": user_id, "amount": amount})
    resp.raise_for_status()
    return resp.json(), time.perf_counter() - t0


@app.post("/transaction")
async def create_transaction(req: TransactionRequest):
    global logging_total_time, counter_total_time

    tx_id = str(uuid.uuid4())
    timestamp = datetime.now(timezone.utc).isoformat()

    async with httpx.AsyncClient(timeout=30.0) as client:
        (log_result, log_t), (counter_result, counter_t) = await asyncio.gather(
            log_post(client, {"transaction_id": tx_id, "user_id": req.user_id, "amount": req.amount}),
            counter_update(client, req.user_id, req.amount),
        )

    logging_total_time += log_t
    counter_total_time += counter_t

    print(f"{tx_id} user={req.user_id} amount={req.amount:+} balance={counter_result['balance']}")

    return {"transaction_id": tx_id, "timestamp": timestamp, "balance": counter_result["balance"]}


@app.get("/user/{user_id}")
async def get_user(user_id: str):
    async with httpx.AsyncClient(timeout=10.0) as client:
        (logs, _), balance_resp = await asyncio.gather(
            log_get(client, f"/logs/{user_id}"),
            client.get(f"{COUNTER_URL}/balance/{user_id}"),
        )

    if balance_resp.status_code == 404:
        raise HTTPException(404, detail=f"user {user_id} not found")
    balance_resp.raise_for_status()

    return {
        "user_id": user_id,
        "balance": balance_resp.json()["balance"],
        "transactions": logs["transactions"],
    }


@app.get("/accounts")
async def get_accounts():
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.get(f"{COUNTER_URL}/balances")
    resp.raise_for_status()
    return resp.json()


@app.get("/logs")
async def get_all_logs():
    async with httpx.AsyncClient(timeout=10.0) as client:
        result, _ = await log_get(client, "/logs")
    return result


@app.get("/stats")
async def get_stats():
    return {
        "logging_total_time_s": round(logging_total_time, 6),
        "counter_total_time_s": round(counter_total_time, 6),
    }


@app.delete("/stats")
async def reset_stats():
    global logging_total_time, counter_total_time
    logging_total_time = 0.0
    counter_total_time = 0.0
    return {"status": "reset"}
