from flask import Flask, request, jsonify
import os
import threading
import time
import requests

app = Flask(__name__)
messages = []
lock = threading.Lock()

PORT = int(os.environ.get('PORT', 8001))
MY_HOST = os.environ.get('MY_HOST', 'logging-service')
CONFIG_SERVER = os.environ.get('CONFIG_SERVER', 'http://config-server:8000')
INSTANCE_ID = os.environ.get('INSTANCE_ID', '1')


@app.route('/log', methods=['POST'])
def log_message():
    data = request.json
    msg = data.get('msg', '')
    with lock:
        messages.append(msg)
    print(f"[logging-service-{INSTANCE_ID}] Received: {msg}", flush=True)
    return jsonify({'status': 'ok'})


@app.route('/messages', methods=['GET'])
def get_messages():
    with lock:
        return jsonify({'messages': list(messages)})


def register():
    time.sleep(3)
    address = f"http://{MY_HOST}:{PORT}"
    for _ in range(10):
        try:
            requests.post(f"{CONFIG_SERVER}/register",
                          json={'name': 'logging-service', 'address': address},
                          timeout=5)
            print(f"[logging-service-{INSTANCE_ID}] Registered at {address}", flush=True)
            return
        except Exception as e:
            print(f"[logging-service-{INSTANCE_ID}] Registration failed: {e}, retrying...", flush=True)
            time.sleep(3)


if __name__ == '__main__':
    t = threading.Thread(target=register, daemon=True)
    t.start()
    app.run(host='0.0.0.0', port=PORT)
