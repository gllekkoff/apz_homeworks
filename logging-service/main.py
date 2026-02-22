from fastapi import FastAPI
from pydantic import BaseModel

app = FastAPI()

transactions: dict[str, dict] = {}


class Transaction(BaseModel):
    transaction_id: str
    user_id: str
    amount: float


@app.post("/log", status_code=201)
async def log_transaction(tx: Transaction):
    transactions[tx.transaction_id] = tx.model_dump()
    print(f"{tx.transaction_id} user={tx.user_id} amount={tx.amount}")
    return {"status": "ok", "transaction_id": tx.transaction_id}


@app.get("/logs/{user_id}")
async def get_user_logs(user_id: str):
    user_txs = [t for t in transactions.values() if t["user_id"] == user_id]
    return {"user_id": user_id, "transactions": user_txs}


@app.get("/logs")
async def get_all_logs():
    return {"transactions": list(transactions.values())}
