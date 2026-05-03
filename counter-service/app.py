from flask import Flask, jsonify
import base64
import json
import os
import threading
import time

import hazelcast
import requests

app = Flask(__name__)

PORT = int(os.environ.get("PORT", 8002))
MY_HOST = os.environ.get("MY_HOST", "counter-service")
INSTANCE_ID = os.environ.get("INSTANCE_ID", MY_HOST)
CONSUL_HTTP_ADDR = os.environ.get("CONSUL_HTTP_ADDR", "http://consul:8500")
SERVICE_NAME = "counter-service"
SERVICE_ID = os.environ.get("SERVICE_ID", f"{SERVICE_NAME}-{INSTANCE_ID}")

BALANCE_MAP = "counter-state"
TOTAL_BALANCE_KEY = "__total__"

hz_client = None
hz_queue = None
balance_map = None
hz_lock = threading.Lock()
metrics = {"processed": 0, "total_ms": 0.0}
metrics_lock = threading.Lock()


def consul_url(path):
    return f"{CONSUL_HTTP_ADDR.rstrip('/')}{path}"


def get_kv(key):
    response = requests.get(consul_url(f"/v1/kv/{key}"), timeout=5)
    if response.status_code == 404:
        raise RuntimeError(f"Missing Consul KV key: {key}")
    response.raise_for_status()
    data = response.json()
    if not data:
        raise RuntimeError(f"Missing Consul KV key: {key}")
    value = data[0].get("Value")
    if value is None:
        raise RuntimeError(f"Missing Consul KV value: {key}")
    return base64.b64decode(value).decode("utf-8")


def get_queue_config():
    members = get_kv("config/message-queue/hazelcast-members")
    queue_name = get_kv("config/message-queue/name")
    return [member.strip() for member in members.split(",") if member.strip()], queue_name


def init_hazelcast():
    global hz_client, hz_queue, balance_map
    with hz_lock:
        if hz_client is None:
            members, queue_name = get_queue_config()
            hz_client = hazelcast.HazelcastClient(cluster_members=members)
            hz_queue = hz_client.get_queue(queue_name).blocking()
            balance_map = hz_client.get_map(BALANCE_MAP).blocking()
            if balance_map.get(TOTAL_BALANCE_KEY) is None:
                balance_map.put(TOTAL_BALANCE_KEY, 0)
            print(
                f"[{SERVICE_ID}] Connected to Hazelcast queue '{queue_name}' via {members}",
                flush=True,
            )
    return hz_queue, balance_map


def parse_transaction(raw_msg):
    try:
        data = json.loads(raw_msg)
    except (TypeError, json.JSONDecodeError):
        return {"account_id": "default", "amount": 1, "msg": str(raw_msg)}
    return {
        "account_id": str(data.get("account_id", data.get("account", "default"))),
        "amount": int(data.get("amount", 1)),
        "msg": data.get("msg", ""),
    }


def increment_key(state, key, amount):
    state.lock(key)
    try:
        current = state.get(key) or 0
        current += amount
        state.put(key, current)
        return current
    finally:
        state.unlock(key)


def increment_balance(state, transaction):
    account_id = transaction["account_id"]
    amount = transaction["amount"]
    account_balance = increment_key(state, account_id, amount)
    total_balance = increment_key(state, TOTAL_BALANCE_KEY, amount)
    return account_balance, total_balance


def consume_queue():
    while True:
        try:
            queue, state = init_hazelcast()
            print(f"[{SERVICE_ID}] Consuming queue...", flush=True)
            while True:
                raw_msg = queue.take()
                transaction = parse_transaction(raw_msg)
                started = time.perf_counter()
                account_balance, total_balance = increment_balance(state, transaction)
                elapsed_ms = (time.perf_counter() - started) * 1000
                with metrics_lock:
                    metrics["processed"] += 1
                    metrics["total_ms"] += elapsed_ms
                print(
                    f"[{SERVICE_ID}] Processed {transaction}, "
                    f"account_balance={account_balance}, total_balance={total_balance}",
                    flush=True,
                )
        except Exception as exc:
            print(f"[{SERVICE_ID}] Queue error: {exc}; reconnecting in 5s...", flush=True)
            time.sleep(5)


def register_service():
    payload = {
        "ID": SERVICE_ID,
        "Name": SERVICE_NAME,
        "Address": MY_HOST,
        "Port": PORT,
        "Check": {
            "HTTP": f"http://{MY_HOST}:{PORT}/health",
            "Interval": "5s",
            "Timeout": "2s",
        },
    }
    while True:
        try:
            response = requests.put(consul_url("/v1/agent/service/register"), json=payload, timeout=5)
            response.raise_for_status()
            print(f"[{SERVICE_ID}] Registered in Consul as {MY_HOST}:{PORT}", flush=True)
            return
        except Exception as exc:
            print(f"[{SERVICE_ID}] Consul registration failed: {exc}; retrying...", flush=True)
            time.sleep(3)


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok", "service": SERVICE_NAME, "id": SERVICE_ID})


@app.route("/balance", methods=["GET"])
def get_balance():
    try:
        _, state = init_hazelcast()
        return jsonify({"balance": state.get(TOTAL_BALANCE_KEY) or 0, "instance": SERVICE_ID})
    except Exception as exc:
        return jsonify({"error": str(exc), "instance": SERVICE_ID}), 503


@app.route("/balances", methods=["GET"])
def get_balances():
    try:
        _, state = init_hazelcast()
        keys = state.key_set()
        balances = {}
        for key in keys:
            if key != TOTAL_BALANCE_KEY:
                balances[str(key)] = state.get(key) or 0
        return jsonify(
            {
                "balance": state.get(TOTAL_BALANCE_KEY) or 0,
                "balances": balances,
                "instance": SERVICE_ID,
            }
        )
    except Exception as exc:
        return jsonify({"error": str(exc), "instance": SERVICE_ID}), 503


@app.route("/balances/reset", methods=["POST"])
def reset_balances():
    try:
        _, state = init_hazelcast()
        state.clear()
        state.put(TOTAL_BALANCE_KEY, 0)
        return jsonify({"status": "ok", "instance": SERVICE_ID})
    except Exception as exc:
        return jsonify({"error": str(exc), "instance": SERVICE_ID}), 503


@app.route("/metrics", methods=["GET"])
def get_metrics():
    with metrics_lock:
        processed = metrics["processed"]
        total_ms = metrics["total_ms"]
    average_ms = total_ms / processed if processed else 0
    return jsonify(
        {
            "service": SERVICE_NAME,
            "instance": SERVICE_ID,
            "processed": processed,
            "total_ms": total_ms,
            "average_ms": average_ms,
        }
    )


@app.route("/metrics/reset", methods=["POST"])
def reset_metrics():
    with metrics_lock:
        metrics["processed"] = 0
        metrics["total_ms"] = 0.0
    return jsonify({"status": "ok", "instance": SERVICE_ID})


if __name__ == "__main__":
    threading.Thread(target=register_service, daemon=True).start()
    threading.Thread(target=consume_queue, daemon=True).start()
    app.run(host="0.0.0.0", port=PORT)
