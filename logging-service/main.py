import asyncio
import logging
import os

import hazelcast
from fastapi import FastAPI
from pydantic import BaseModel

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
logger = logging.getLogger(__name__)

INSTANCE_NAME = os.getenv("INSTANCE_NAME", "logging-service")
HAZELCAST_NODES = os.getenv(
    "HAZELCAST_NODES", "hazelcast1:5701,hazelcast2:5701,hazelcast3:5701"
).split(",")

app = FastAPI()

hz_client: hazelcast.HazelcastClient | None = None
hz_map = None


@app.on_event("startup")
async def startup():
    global hz_client, hz_map
    loop = asyncio.get_running_loop()
    hz_client = await loop.run_in_executor(
        None,
        lambda: hazelcast.HazelcastClient(
            cluster_name="dev",
            cluster_members=HAZELCAST_NODES,
            cluster_connect_timeout=60.0,
        ),
    )
    hz_map = hz_client.get_map("transactions").blocking()
    logger.info("[%s] connected to Hazelcast %s", INSTANCE_NAME, HAZELCAST_NODES)


@app.on_event("shutdown")
async def shutdown():
    if hz_client:
        hz_client.shutdown()


class Transaction(BaseModel):
    transaction_id: str
    user_id: str
    amount: float


@app.post("/log", status_code=201)
async def log_transaction(tx: Transaction):
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, lambda: hz_map.put(tx.transaction_id, tx.model_dump()))
    logger.info("[%s] logged %s user=%s amount=%s", INSTANCE_NAME, tx.transaction_id, tx.user_id, tx.amount)
    return {"status": "ok", "transaction_id": tx.transaction_id}


@app.get("/logs/{user_id}")
async def get_user_logs(user_id: str):
    loop = asyncio.get_running_loop()
    all_txs = await loop.run_in_executor(None, hz_map.values)
    result = [t for t in all_txs if t["user_id"] == user_id]
    logger.info("[%s] GET /logs/%s -> %d entries", INSTANCE_NAME, user_id, len(result))
    return {"user_id": user_id, "transactions": result}


@app.get("/logs")
async def get_all_logs():
    loop = asyncio.get_running_loop()
    txs = list(await loop.run_in_executor(None, hz_map.values))
    logger.info("[%s] GET /logs -> %d entries", INSTANCE_NAME, len(txs))
    return {"transactions": txs}
