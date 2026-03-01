from hz_client import connect

client = connect()
m = client.get_map("demo-map").blocking()

for i in range(1000):
    m.put(i, f"value-{i}")

print(f"Done. Map size: {m.size()}")
client.shutdown()
