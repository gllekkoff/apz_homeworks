import sys

from hz_client import connect

client = connect()
queue = client.get_queue("bounded-queue").blocking()
cid = sys.argv[1] if len(sys.argv) > 1 else "C"

for _ in range(50):
    value = queue.take()
    print(f"[{cid}] received: {value}")

print(f"[{cid}] done")
client.shutdown()
