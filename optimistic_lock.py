import time

from hz_client import connect

client = connect()
m = client.get_map("counter-map").blocking()
m.put_if_absent("key", 0)

start = time.time()
retries = 0

for _ in range(10_000):
    while True:
        old_value = m.get("key")
        new_value = old_value + 1
        if m.replace_if_same("key", old_value, new_value):
            break
        retries += 1

elapsed = (time.time() - start) * 1000
print(
    f"[optimistic] time: {elapsed:.0f} ms | result: {m.get('key')} | retries: {retries}"
)
client.shutdown()
