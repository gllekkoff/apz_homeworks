from flask import Flask, request, jsonify
import base64
import json
import os
import random
import threading
import time

import hazelcast
import requests

app = Flask(__name__)

PORT = int(os.environ.get("PORT", 8080))
MY_HOST = os.environ.get("MY_HOST", "facade-service")
INSTANCE_ID = os.environ.get("INSTANCE_ID", MY_HOST)
CONSUL_HTTP_ADDR = os.environ.get("CONSUL_HTTP_ADDR", "http://consul:8500")
SERVICE_NAME = "facade-service"
SERVICE_ID = os.environ.get("SERVICE_ID", f"{SERVICE_NAME}-{INSTANCE_ID}")

hz_queue = None
hz_lock = threading.Lock()


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


def get_queue():
    global hz_queue
    with hz_lock:
        if hz_queue is None:
            members, queue_name = get_queue_config()
            client = hazelcast.HazelcastClient(cluster_members=members)
            hz_queue = client.get_queue(queue_name).blocking()
            print(
                f"[{SERVICE_ID}] Connected to Hazelcast queue '{queue_name}' via {members}",
                flush=True,
            )
    return hz_queue


def discover_services(name):
    response = requests.get(consul_url(f"/v1/health/service/{name}"), params={"passing": "true"}, timeout=5)
    response.raise_for_status()
    addresses = []
    for item in response.json():
        service = item.get("Service", {})
        address = service.get("Address") or item.get("Node", {}).get("Address")
        port = service.get("Port")
        if address and port:
            addresses.append(f"http://{address}:{port}")
    return addresses


def discover_service(name):
    addresses = discover_services(name)
    if not addresses:
        return None
    return random.choice(addresses)


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


@app.route("/message", methods=["POST"])
def post_message():
    data = request.get_json(silent=True) or {}
    account_id = str(data.get("account_id", data.get("account", "default")))
    amount = int(data.get("amount", 1))
    msg = data.get("msg", f"{account_id}:{amount}")
    transaction = {"account_id": account_id, "amount": amount, "msg": msg}

    get_queue().put(json.dumps(transaction))
    print(f"[{SERVICE_ID}] Queued transaction: {transaction}", flush=True)

    log_addr = discover_service("logging-service")
    if log_addr:
        requests.post(f"{log_addr}/log", json=transaction, timeout=5)
        print(f"[{SERVICE_ID}] Logged to {log_addr}", flush=True)

    return jsonify({"status": "ok", "transaction": transaction})


@app.route("/messages", methods=["GET"])
def get_messages():
    messages = []
    for log_addr in discover_services("logging-service"):
        try:
            response = requests.get(f"{log_addr}/messages", timeout=5)
            response.raise_for_status()
            messages.extend(response.json().get("messages", []))
        except Exception as exc:
            print(f"[{SERVICE_ID}] Logging fetch error: {exc}", flush=True)

    balance = None
    balances = {}
    counter_addr = discover_service("counter-service")
    if counter_addr:
        try:
            response = requests.get(f"{counter_addr}/balance", timeout=5)
            response.raise_for_status()
            balance = response.json().get("balance")
            response = requests.get(f"{counter_addr}/balances", timeout=5)
            response.raise_for_status()
            balances = response.json().get("balances", {})
        except Exception as exc:
            print(f"[{SERVICE_ID}] Counter fetch error: {exc}", flush=True)

    return jsonify({"messages": messages, "balance": balance, "balances": balances})


def init_hazelcast():
    while True:
        try:
            get_queue()
            return
        except Exception as exc:
            print(f"[{SERVICE_ID}] Hazelcast not ready: {exc}; retrying...", flush=True)
            time.sleep(5)


if __name__ == "__main__":
    threading.Thread(target=register_service, daemon=True).start()
    threading.Thread(target=init_hazelcast, daemon=True).start()
    app.run(host="0.0.0.0", port=PORT)
