from flask import Flask, request, jsonify
import base64
import os
import threading
import time

import hazelcast
import requests

app = Flask(__name__)

messages = []
messages_lock = threading.Lock()
metrics = {"requests": 0, "total_ms": 0.0}
metrics_lock = threading.Lock()
hz_client = None
hz_lock = threading.Lock()

PORT = int(os.environ.get("PORT", 8001))
MY_HOST = os.environ.get("MY_HOST", "logging-service")
INSTANCE_ID = os.environ.get("INSTANCE_ID", MY_HOST)
CONSUL_HTTP_ADDR = os.environ.get("CONSUL_HTTP_ADDR", "http://consul:8500")
SERVICE_NAME = "logging-service"
SERVICE_ID = os.environ.get("SERVICE_ID", f"{SERVICE_NAME}-{INSTANCE_ID}")

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


def get_hazelcast_members():
    members = get_kv("config/hazelcast/members")
    return [member.strip() for member in members.split(",") if member.strip()]


def init_hazelcast():
    global hz_client
    while True:
        try:
            with hz_lock:
                if hz_client is None:
                    members = get_hazelcast_members()
                    hz_client = hazelcast.HazelcastClient(cluster_members=members)
                    print(f"[{SERVICE_ID}] Loaded Hazelcast config from Consul: {members}", flush=True)
            return
        except Exception as exc:
            print(f"[{SERVICE_ID}] Hazelcast config/client not ready: {exc}; retrying...", flush=True)
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


@app.route("/log", methods=["POST"])
def log_message():
    started = time.perf_counter()
    data = request.get_json(silent=True) or {}
    msg = data.get("msg", "")
    with messages_lock:
        messages.append(msg)
    elapsed_ms = (time.perf_counter() - started) * 1000
    with metrics_lock:
        metrics["requests"] += 1
        metrics["total_ms"] += elapsed_ms
    print(f"[{SERVICE_ID}] Received: {msg}", flush=True)
    return jsonify({"status": "ok", "instance": SERVICE_ID})


@app.route("/messages", methods=["GET"])
def get_messages():
    with messages_lock:
        return jsonify({"messages": list(messages), "instance": SERVICE_ID})


@app.route("/metrics", methods=["GET"])
def get_metrics():
    with metrics_lock:
        requests_count = metrics["requests"]
        total_ms = metrics["total_ms"]
    average_ms = total_ms / requests_count if requests_count else 0
    return jsonify(
        {
            "service": SERVICE_NAME,
            "instance": SERVICE_ID,
            "requests": requests_count,
            "total_ms": total_ms,
            "average_ms": average_ms,
        }
    )


@app.route("/metrics/reset", methods=["POST"])
def reset_metrics():
    with metrics_lock:
        metrics["requests"] = 0
        metrics["total_ms"] = 0.0
    return jsonify({"status": "ok", "instance": SERVICE_ID})


if __name__ == "__main__":
    threading.Thread(target=register_service, daemon=True).start()
    threading.Thread(target=init_hazelcast, daemon=True).start()
    app.run(host="0.0.0.0", port=PORT)
