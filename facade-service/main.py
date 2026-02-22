import asyncio
import time
import uuid
from datetime import datetime, timezone

import httpx
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

app = FastAPI()

LOGGING_URL = "http://logging-service:8081"
COUNTER_URL = "http://counter-service:8082"

logging_total_time: float = 0.0
counter_total_time: float = 0.0


class TransactionRequest(BaseModel):
    user_id: str
    amount: float


async def call_logging(client: httpx.AsyncClient, payload: dict) -> tuple[dict, float]:
    t0 = time.perf_counter()
    resp = await client.post(f"{LOGGING_URL}/log", json=payload)
    elapsed = time.perf_counter() - t0
    resp.raise_for_status()
    return resp.json(), elapsed


async def call_counter(client: httpx.AsyncClient, user_id: str, amount: float) -> tuple[dict, float]:
    t0 = time.perf_counter()
    resp = await client.post(f"{COUNTER_URL}/update", json={"user_id": user_id, "amount": amount})
    elapsed = time.perf_counter() - t0
    resp.raise_for_status()
    return resp.json(), elapsed


@app.post("/transaction")
async def create_transaction(req: TransactionRequest):
    global logging_total_time, counter_total_time

    transaction_id = str(uuid.uuid4())
    timestamp = datetime.now(timezone.utc).isoformat()

    log_payload = {
        "transaction_id": transaction_id,
        "user_id": req.user_id,
        "amount": req.amount,
    }

    async with httpx.AsyncClient(timeout=30.0) as client:
        (log_result, log_elapsed), (counter_result, counter_elapsed) = await asyncio.gather(
            call_logging(client, log_payload),
            call_counter(client, req.user_id, req.amount),
        )

    logging_total_time += log_elapsed
    counter_total_time += counter_elapsed

    print(f"{transaction_id} user={req.user_id} amount={req.amount:+} balance={counter_result['balance']}")

    return {
        "transaction_id": transaction_id,
        "timestamp": timestamp,
        "balance": counter_result["balance"],
    }


@app.get("/user/{user_id}")
async def get_user(user_id: str):
    async with httpx.AsyncClient(timeout=10.0) as client:
        balance_resp, logs_resp = await asyncio.gather(
            client.get(f"{COUNTER_URL}/balance/{user_id}"),
            client.get(f"{LOGGING_URL}/logs/{user_id}"),
        )

    if balance_resp.status_code == 404:
        raise HTTPException(status_code=404, detail=f"user {user_id} not found")

    balance_resp.raise_for_status()
    logs_resp.raise_for_status()

    return {
        "user_id": user_id,
        "balance": balance_resp.json()["balance"],
        "transactions": logs_resp.json()["transactions"],
    }


@app.get("/accounts")
async def get_accounts():
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.get(f"{COUNTER_URL}/balances")
    resp.raise_for_status()
    return resp.json()


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
