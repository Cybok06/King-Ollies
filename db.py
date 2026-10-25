from pymongo.mongo_client import MongoClient
from pymongo.server_api import ServerApi
from pymongo.errors import ConfigurationError, PyMongoError

# MongoDB Atlas URI
uri = "mongodb://thekobanitours_db_user:1NDiC8icYCF2Kgki@ac-egsviry-shard-00-00.tbhgqk3.mongodb.net:27017,ac-egsviry-shard-00-01.tbhgqk3.mongodb.net:27017,ac-egsviry-shard-00-02.tbhgqk3.mongodb.net:27017/?ssl=true&replicaSet=atlas-j1rrrf-shard-0&authSource=admin&appName=Cluster0"

# Validate the client configuration and verify the connection.
try:
    client = MongoClient(
        uri,
        server_api=ServerApi("1"),
        connectTimeoutMS=10000,
        serverSelectionTimeoutMS=10000,
    )
    client.admin.command("ping")
except ConfigurationError as exc:
    raise SystemExit(
        "MongoDB configuration failed. Check the connection string and options.\n"
        f"Details: {exc}"
    ) from None
except PyMongoError as exc:
    raise SystemExit(f"MongoDB connection failed: {exc}") from None

print("Successfully connected to MongoDB!")

# Access your database
db = client["kingollies"]
