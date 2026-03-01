import hazelcast


def connect():
    client = hazelcast.HazelcastClient(
        cluster_name="dev",
        cluster_members=[
            "localhost:5701",
            "localhost:5702",
            "localhost:5703",
        ],
    )
    return client
