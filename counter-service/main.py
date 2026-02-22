import asyncio
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

app = FastAPI()

balances: dict[str, float] = {}
_lock = asyncio.Lock()


class UpdateRequest(BaseModel):
    user_id: str
    amount: float


@app.post("/update")
async def update_balance(req: UpdateRequest):
    async with _lock:
        balances[req.user_id] = balances.get(req.user_id, 0.0) + req.amount
        new_balance = balances[req.user_id]
    print(f"user={req.user_id} amount={req.amount:+} balance={new_balance}")
    return {"user_id": req.user_id, "balance": new_balance}


@app.get("/balance/{user_id}")
async def get_balance(user_id: str):
    if user_id not in balances:
        raise HTTPException(status_code=404, detail=f"user {user_id} not found")
    return {"user_id": user_id, "balance": balances[user_id]}


@app.get("/balances")
async def get_all_balances():
    return {"balances": dict(balances)}
