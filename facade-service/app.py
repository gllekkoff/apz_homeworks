from flask import Flask, request, jsonify
import os
import threading
import time
import random
import requests
import hazelcast

app = Flask(__name__)

PORT = int(os.environ.get('PORT', 8080))
MY_HOST = os.environ.get('MY_HOST', 'facade-service')
CONFIG_SERVER = os.environ.get('CONFIG_SERVER', 'http://config-server:8000')
HZ_MEMBERS = os.environ.get('HZ_MEMBERS', 'hazelcast1:5701,hazelcast2:5701,hazelcast3:5701').split(',')
QUEUE_NAME = 'counter-queue'

hz_queue = None
hz_lock = threading.Lock()


def get_queue():
    global hz_queue
    with hz_lock:
        if hz_queue is None:
            client = hazelcast.HazelcastClient(cluster_members=HZ_MEMBERS)
            hz_queue = client.get_queue(QUEUE_NAME).blocking()
            print("[facade-service] Connected to Hazelcast queue", flush=True)
    return hz_queue


def get_service_address(name):
    resp = requests.get(f"{CONFIG_SERVER}/service/{name}", timeout=5)
    addresses = resp.json().get('addresses', [])
    if not addresses:
        return None
    return random.choice(addresses)


@app.route('/message', methods=['POST'])
def post_message():
    data = request.json
    msg = data.get('msg', '')

    get_queue().put(msg)
    print(f"[facade-service] Queued message: {msg}", flush=True)

    log_addr = get_service_address('logging-service')
    if log_addr:
        requests.post(f"{log_addr}/log", json={'msg': msg}, timeout=5)
        print(f"[facade-service] Logged to {log_addr}", flush=True)

    return jsonify({'status': 'ok', 'msg': msg})


@app.route('/messages', methods=['GET'])
def get_messages():
    log_addr = get_service_address('logging-service')
    messages = []
    if log_addr:
        try:
            resp = requests.get(f"{log_addr}/messages", timeout=5)
            messages = resp.json().get('messages', [])
        except Exception as e:
            print(f"[facade-service] Logging fetch error: {e}", flush=True)

    balance = None
    counter_addr = get_service_address('counter-service')
    if counter_addr:
        try:
            resp = requests.get(f"{counter_addr}/balance", timeout=5)
            balance = resp.json().get('balance')
        except Exception as e:
            print(f"[facade-service] Counter fetch error: {e}", flush=True)

    return jsonify({'messages': messages, 'balance': balance})


def register():
    time.sleep(3)
    address = f"http://{MY_HOST}:{PORT}"
    for _ in range(10):
        try:
            requests.post(f"{CONFIG_SERVER}/register",
                          json={'name': 'facade-service', 'address': address},
                          timeout=5)
            print(f"[facade-service] Registered at {address}", flush=True)
            return
        except Exception as e:
            print(f"[facade-service] Registration failed: {e}, retrying...", flush=True)
            time.sleep(3)


def init_hazelcast():
    time.sleep(5)
    while True:
        try:
            get_queue()
            return
        except Exception as e:
            print(f"[facade-service] Hazelcast not ready: {e}, retrying...", flush=True)
            time.sleep(5)


if __name__ == '__main__':
    threading.Thread(target=register, daemon=True).start()
    threading.Thread(target=init_hazelcast, daemon=True).start()
    app.run(host='0.0.0.0', port=PORT)
