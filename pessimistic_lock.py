import time

from hz_client import connect

client = connect()
m = client.get_map("counter-map").blocking()
m.put_if_absent("key", 0)

start = time.time()
for _ in range(10_000):
    m.lock("key")
    try:
        value = m.get("key")
        value += 1
        m.put("key", value)
    finally:
        m.unlock("key")

elapsed = (time.time() - start) * 1000
print(f"[pessimistic] time: {elapsed:.0f} ms | result: {m.get('key')}")
client.shutdown()
