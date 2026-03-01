from hz_client import connect

client = connect()
m = client.get_map("counter-map").blocking()
m.put("key", 0)
print(f"Counter reset. Current value: {m.get('key')}")
client.shutdown()
