#!/usr/bin/env python3
import argparse
import concurrent.futures
import json
import time
from urllib import parse, request


HOST_PORTS = {
    "logging-service-1": 8011,
    "logging-service-2": 8012,
    "logging-service-3": 8013,
    "counter-service-1": 8002,
    "counter-service-2": 8003,
    "facade-service-1": 8080,
    "facade-service-2": 8081,
}


def http_json(method, url, payload=None, timeout=30):
    data = None
    headers = {}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = request.Request(url, data=data, headers=headers, method=method)
    with request.urlopen(req, timeout=timeout) as response:
        body = response.read().decode("utf-8")
    return json.loads(body) if body else {}


def to_host_address(address):
    parsed = parse.urlparse(address)
    if parsed.hostname in HOST_PORTS:
        return f"{parsed.scheme}://localhost:{HOST_PORTS[parsed.hostname]}"
    return address


def discover(consul_url, service_name, host_mode=True):
    url = f"{consul_url.rstrip('/')}/v1/health/service/{service_name}?passing=true"
    items = http_json("GET", url)
    addresses = []
    for item in items:
        service = item["Service"]
        address = f"http://{service['Address']}:{service['Port']}"
        addresses.append(to_host_address(address) if host_mode else address)
    return addresses


def wait_for_instances(consul_url, service_name, expected_count, host_mode, timeout=60):
    deadline = time.monotonic() + timeout
    addresses = []
    while time.monotonic() < deadline:
        addresses = discover(consul_url, service_name, host_mode=host_mode)
        if len(addresses) >= expected_count:
            return addresses
        time.sleep(1)
    raise TimeoutError(
        f"Expected {expected_count} healthy {service_name} instances, "
        f"but found {len(addresses)}: {addresses}"
    )


def reset_logging(logging_addresses):
    for address in logging_addresses:
        http_json("POST", f"{address}/metrics/reset", {})


def reset_counter(counter_addresses):
    for address in counter_addresses:
        http_json("POST", f"{address}/metrics/reset", {})
    http_json("POST", f"{counter_addresses[0]}/balances/reset", {})


def sum_logging_metrics(addresses):
    metrics = [http_json("GET", f"{address}/metrics") for address in addresses]
    return sum(item["requests"] for item in metrics), sum(item["total_ms"] for item in metrics)


def sum_counter_metrics(addresses):
    metrics = [http_json("GET", f"{address}/metrics") for address in addresses]
    return sum(item["processed"] for item in metrics), sum(item["total_ms"] for item in metrics)


def get_balances(counter_addresses):
    return http_json("GET", f"{counter_addresses[0]}/balances")


def wait_for_balances(counter_addresses, expected_balances, expected_total, timeout):
    deadline = time.monotonic() + timeout
    current = {}
    while time.monotonic() < deadline:
        current = get_balances(counter_addresses)
        balances = current.get("balances", {})
        total = current.get("balance", 0)
        if total == expected_total and all(balances.get(key) == value for key, value in expected_balances.items()):
            return current
        time.sleep(0.5)
    raise TimeoutError(f"balances did not reach expected values. Last state: {current}")


def post_transactions(client_id, transactions_per_client, accounts, facade_urls):
    facade_url = facade_urls[client_id % len(facade_urls)].rstrip("/")
    for index in range(transactions_per_client):
        account_id = accounts[client_id]
        http_json(
            "POST",
            f"{facade_url}/message",
            {
                "account_id": account_id,
                "amount": 1,
                "msg": f"{account_id}-tx-{index}",
            },
        )


def run_scenario(name, accounts, clients, transactions_per_client, facade_urls, logging_addresses, counter_addresses):
    reset_logging(logging_addresses)
    reset_counter(counter_addresses)

    expected_total = clients * transactions_per_client
    expected_balances = {}
    for account_id in accounts:
        expected_balances[account_id] = expected_balances.get(account_id, 0) + transactions_per_client

    started = time.perf_counter()
    with concurrent.futures.ThreadPoolExecutor(max_workers=clients) as executor:
        futures = [
            executor.submit(post_transactions, client_id, transactions_per_client, accounts, facade_urls)
            for client_id in range(clients)
        ]
        for future in concurrent.futures.as_completed(futures):
            future.result()

    wait_for_balances(counter_addresses, expected_balances, expected_total, timeout=300)
    total_seconds = time.perf_counter() - started

    logging_requests, logging_ms = sum_logging_metrics(logging_addresses)
    counter_processed, counter_ms = sum_counter_metrics(counter_addresses)

    return {
        "scenario": name,
        "clients": clients,
        "transactions_per_client": transactions_per_client,
        "total_requests": expected_total,
        "total_seconds": total_seconds,
        "requests_per_second": expected_total / total_seconds,
        "logging_requests": logging_requests,
        "logging_ms": logging_ms,
        "counter_processed": counter_processed,
        "counter_ms": counter_ms,
        "expected_balances": expected_balances,
        "actual_balances": get_balances(counter_addresses).get("balances", {}),
    }


def print_result(result):
    print(f"\n{result['scenario']}")
    print(f"  clients: {result['clients']}")
    print(f"  transactions/client: {result['transactions_per_client']}")
    print(f"  total requests: {result['total_requests']}")
    print(f"  total time: {result['total_seconds']:.3f} s")
    print(f"  requests/sec: {result['requests_per_second']:.2f}")
    print(
        f"  logging-service contribution: {result['logging_ms']:.2f} ms "
        f"({result['logging_requests']} requests)"
    )
    print(
        f"  counter-service contribution: {result['counter_ms']:.2f} ms "
        f"({result['counter_processed']} processed)"
    )
    print(f"  balances correct: {result['actual_balances'] == result['expected_balances']}")


def main():
    parser = argparse.ArgumentParser(description="Run Lab 5 concurrent performance scenarios.")
    parser.add_argument("--facade-url", action="append")
    parser.add_argument("--consul-url", default="http://localhost:8500")
    parser.add_argument("--clients", type=int, default=10)
    parser.add_argument("--transactions-per-client", type=int, default=10000)
    parser.add_argument("--expected-logging-instances", type=int, default=3)
    parser.add_argument("--expected-counter-instances", type=int, default=2)
    parser.add_argument(
        "--docker-network-addresses",
        action="store_true",
        help="Use Docker DNS names returned by Consul instead of localhost published ports.",
    )
    args = parser.parse_args()
    facade_urls = args.facade_url or ["http://localhost:8080", "http://localhost:8081"]

    host_mode = not args.docker_network_addresses
    logging_addresses = wait_for_instances(
        args.consul_url,
        "logging-service",
        args.expected_logging_instances,
        host_mode=host_mode,
    )
    counter_addresses = wait_for_instances(
        args.consul_url,
        "counter-service",
        args.expected_counter_instances,
        host_mode=host_mode,
    )

    print("Discovered services:")
    print(f"  logging-service: {', '.join(logging_addresses)}")
    print(f"  counter-service: {', '.join(counter_addresses)}")
    print(f"  facade-service clients will use: {', '.join(facade_urls)}")

    separate_accounts = [f"account-{client_id}" for client_id in range(args.clients)]
    shared_account = ["shared-account" for _ in range(args.clients)]

    results = [
        run_scenario(
            f"{args.clients} clients x {args.transactions_per_client} transactions to {args.clients} different accounts",
            separate_accounts,
            args.clients,
            args.transactions_per_client,
            facade_urls,
            logging_addresses,
            counter_addresses,
        ),
        run_scenario(
            f"{args.clients} clients x {args.transactions_per_client} transactions to one shared account",
            shared_account,
            args.clients,
            args.transactions_per_client,
            facade_urls,
            logging_addresses,
            counter_addresses,
        ),
    ]

    for result in results:
        print_result(result)

    print("\nTask 5 table values:")
    for result in results:
        print(
            f"{result['scenario']} | Total time: {result['total_seconds']:.3f} s | "
            f"Requests/sec: {result['requests_per_second']:.2f} | "
            f"logging-service contribution: {result['logging_ms']:.2f} ms | "
            f"counter-service contribution: {result['counter_ms']:.2f} ms"
        )


if __name__ == "__main__":
    main()
