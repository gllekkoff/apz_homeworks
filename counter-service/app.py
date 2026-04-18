from flask import Flask, jsonify
import os
import threading
import time
import requests
import hazelcast

app = Flask(__name__)

PORT = int(os.environ.get('PORT', 8002))
MY_HOST = os.environ.get('MY_HOST', 'counter-service')
CONFIG_SERVER = os.environ.get('CONFIG_SERVER', 'http://config-server:8000')
HZ_MEMBERS = os.environ.get('HZ_MEMBERS', 'hazelcast1:5701,hazelcast2:5701,hazelcast3:5701').split(',')
QUEUE_NAME = 'counter-queue'

balance = 0
balance_lock = threading.Lock()


def consume_queue():
    global balance
    while True:
        try:
            print("[counter-service] Connecting to Hazelcast...", flush=True)
            client = hazelcast.HazelcastClient(cluster_members=HZ_MEMBERS)
            queue = client.get_queue(QUEUE_NAME).blocking()
            print("[counter-service] Connected. Consuming queue...", flush=True)
            while True:
                msg = queue.take()
                with balance_lock:
                    balance += 1
                    current = balance
                print(f"[counter-service] Processed '{msg}', balance={current}", flush=True)
        except Exception as e:
            print(f"[counter-service] Queue error: {e}, reconnecting in 5s...", flush=True)
            time.sleep(5)


@app.route('/balance', methods=['GET'])
def get_balance():
    with balance_lock:
        return jsonify({'balance': balance})


def register():
    time.sleep(3)
    address = f"http://{MY_HOST}:{PORT}"
    for _ in range(10):
        try:
            requests.post(f"{CONFIG_SERVER}/register",
                          json={'name': 'counter-service', 'address': address},
                          timeout=5)
            print(f"[counter-service] Registered at {address}", flush=True)
            return
        except Exception as e:
            print(f"[counter-service] Registration failed: {e}, retrying...", flush=True)
            time.sleep(3)


if __name__ == '__main__':
    threading.Thread(target=register, daemon=True).start()
    threading.Thread(target=consume_queue, daemon=True).start()
    app.run(host='0.0.0.0', port=PORT)
