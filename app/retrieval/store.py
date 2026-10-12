import logging
import os
import warnings

from app.retrieval.embeddings import LocalTokenEmbedder

COLLECTION_NAME = "shift_documents"
_CLIENTS = {}


class RetrievalUnavailable(Exception):
    """The vector store could not be read or written."""


def upsert_documents(path, *, ids, documents, metadatas, delete_where=None):
    collection = _collection(path)
    if delete_where:
        _delete_where(collection, delete_where)
    if documents:
        try:
            collection.add(ids=ids, documents=documents, metadatas=metadatas)
        except Exception as error:
            raise RetrievalUnavailable("Semantic search is unavailable.") from error


def query_documents(path, text, *, limit, where):
    collection = _collection(path)
    try:
        count = collection.count()
    except Exception as error:
        raise RetrievalUnavailable("Semantic search is unavailable.") from error
    if count == 0:
        return []
    try:
        result = collection.query(
            query_texts=[text],
            n_results=min(limit, count),
            where=where,
        )
    except Exception as error:
        message = str(error).lower()
        if "0 results" in message or "cannot be greater" in message:
            return []
        raise RetrievalUnavailable("Semantic search is unavailable.") from error
    return _rows(result)


def has_clause(path, clause_id):
    collection = _collection(path)
    try:
        found = collection.get(where={"clause_id": clause_id}, limit=1)
    except Exception as error:
        raise RetrievalUnavailable("Semantic search is unavailable.") from error
    return bool(found.get("ids"))


def _collection(path):
    os.environ["ANONYMIZED_TELEMETRY"] = "False"
    logging.getLogger("chromadb.telemetry.product.posthog").setLevel(logging.CRITICAL)
    warnings.filterwarnings(
        "ignore",
        message="Accessing the 'model_fields' attribute",
        category=DeprecationWarning,
    )
    try:
        import chromadb
        from chromadb.config import Settings

        os.makedirs(path, exist_ok=True)
        key = os.path.abspath(path)
        client = _CLIENTS.get(key)
        if client is None:
            client = chromadb.PersistentClient(
                path=key,
                settings=Settings(anonymized_telemetry=False),
            )
            _CLIENTS[key] = client
        return client.get_or_create_collection(
            name=COLLECTION_NAME,
            embedding_function=LocalTokenEmbedder(),
            metadata={"hnsw:space": "cosine"},
        )
    except RetrievalUnavailable:
        raise
    except Exception as error:
        raise RetrievalUnavailable("Semantic search is unavailable.") from error


def _delete_where(collection, where):
    try:
        collection.delete(where=where)
    except Exception as error:
        message = str(error).lower()
        if "no" in message and "id" in message:
            return
        raise RetrievalUnavailable("Semantic search is unavailable.") from error


def _rows(result):
    documents = (result.get("documents") or [[]])[0] or []
    metadatas = (result.get("metadatas") or [[]])[0] or []
    distances = (result.get("distances") or [[]])[0] or []
    rows = []
    for index, document in enumerate(documents):
        metadata = metadatas[index] if index < len(metadatas) and metadatas[index] else {}
        distance = distances[index] if index < len(distances) else None
        rows.append(
            {
                "document_type": metadata.get("document_type") or "",
                "employee_id": metadata.get("employee_id") or "",
                "clause_id": metadata.get("clause_id") or "",
                "passage": document,
                "distance": distance,
            }
        )
    return rows
