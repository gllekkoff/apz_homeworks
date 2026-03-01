import time

from hz_client import connect

client = connect()
queue = client.get_queue("bounded-queue").blocking()

for i in range(1, 101):
    queue.put(i)
    print(f"[producer] sent: {i}")
    time.sleep(0.05)

print("[producer] done")
client.shutdown()
