"""LangChain-compatible client; the local service owns the only model instance."""

from langchain_core.embeddings import Embeddings

from qareen_client import call


class RemoteEmbeddings(Embeddings):
    def embed_documents(self, texts):
        result = []
        for start in range(0, len(texts), 32):
            result.extend(call("/embed", {"texts": texts[start : start + 32]})["embeddings"])
        return result

    def embed_query(self, text):
        return self.embed_documents([text])[0]
