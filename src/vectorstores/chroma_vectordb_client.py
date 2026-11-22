import chromadb
from chromadb.api.types import Documents, Embedding, Embeddings, IDs, Metadatas, SparseVector
from chromadb.config import Settings


class HttpChromaClient:
    def __init__(self, host: str = "localhost", port: int = 8000):
        self.client = chromadb.HttpClient(host=host, port=port)

    def create_collection(self, name: str):
        return self.client.create_collection(name=name)

    def get_collection(self, name: str):
        return self.client.get_collection(name=name)

    def list_collections(self):
        return self.client.list_collections()

    def delete_collection(self, name: str):
        collection = self.get_collection(name)
        collection.delete()
