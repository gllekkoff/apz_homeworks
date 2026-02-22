import argparse
import threading
import time
import urllib.request
import json

FACADE_URL = "http://localhost:8080"
N_CLIENTS = 10
N_TXNS = 10000

_lock = threading.Lock()
_done = 0


def post_transaction(user_id: str, amount: float):
    data = json.dumps({"user_id": user_id, "amount": amount}).encode()
    req = urllib.request.Request(
        f"{FACADE_URL}/transaction",
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())


def worker(user_id: str, count: int, amount: float, results: list, idx: int, total: int):
    global _done
    ok = 0
    for _ in range(count):
        try:
            post_transaction(user_id, amount)
            ok += 1
        except Exception as e:
            print(f"error user={user_id}: {e}")
        with _lock:
            _done += 1
            if _done % 10000 == 0:
                print(f"  {_done}/{total}")
    results[idx] = ok


def get_accounts() -> dict:
    with urllib.request.urlopen(f"{FACADE_URL}/accounts", timeout=10) as resp:
        return json.loads(resp.read())


def reset_stats():
    req = urllib.request.Request(f"{FACADE_URL}/stats", method="DELETE")
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read())


def get_stats() -> dict:
    with urllib.request.urlopen(f"{FACADE_URL}/stats", timeout=10) as resp:
        return json.loads(resp.read())


def run_scenario(name: str, tasks: list[tuple[str, int, float]]):
    global _done
    print(f"\n--- {name} ---")
    reset_stats()

    total = sum(t[1] for t in tasks)
    results = [0] * len(tasks)
    _done = 0

    threads = [
        threading.Thread(target=worker, args=(uid, count, amt, results, i, total))
        for i, (uid, count, amt) in enumerate(tasks)
    ]

    start = time.perf_counter()
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    elapsed = time.perf_counter() - start

    ok = sum(results)
    print(f"{ok}/{total} ok  {elapsed:.3f}s  {ok/elapsed:.1f} req/s")

    stats = get_stats()
    print(f"logging={stats['logging_total_time_s']:.3f}s  counter={stats['counter_total_time_s']:.3f}s")

    accounts = get_accounts()
    for uid, bal in sorted(accounts["balances"].items()):
        print(f"  {uid}: {bal}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--scenario", choices=["1", "2"], required=True)
    args = parser.parse_args()

    if args.scenario == "1":
        tasks = [(f"user_{i:02d}", N_TXNS, 1.0) for i in range(N_CLIENTS)]
        run_scenario(f"scenario 1: {N_CLIENTS} clients x {N_TXNS} txns, own accounts", tasks)
    else:
        tasks = [("shared_user", N_TXNS, 1.0) for _ in range(N_CLIENTS)]
        run_scenario(f"scenario 2: {N_CLIENTS} clients x {N_TXNS} txns, shared account", tasks)


if __name__ == "__main__":
    main()
