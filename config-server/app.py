from flask import Flask, request, jsonify
import threading

app = Flask(__name__)
registry = {}
lock = threading.Lock()


@app.route('/register', methods=['POST'])
def register():
    data = request.json
    name = data['name']
    address = data['address']
    with lock:
        if name not in registry:
            registry[name] = []
        if address not in registry[name]:
            registry[name].append(address)
    print(f"[config-server] Registered {name} @ {address}", flush=True)
    return jsonify({'status': 'ok'})


@app.route('/service/<name>', methods=['GET'])
def get_service(name):
    with lock:
        addresses = list(registry.get(name, []))
    return jsonify({'addresses': addresses})


@app.route('/registry', methods=['GET'])
def get_registry():
    with lock:
        return jsonify(dict(registry))


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8000)
