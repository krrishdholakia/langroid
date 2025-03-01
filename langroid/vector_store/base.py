import logging
from abc import ABC, abstractmethod
from typing import List, Optional, Sequence, Tuple

from pydantic import BaseSettings

from langroid.embedding_models.base import EmbeddingModelsConfig
from langroid.embedding_models.models import OpenAIEmbeddingsConfig
from langroid.mytypes import Document
from langroid.utils.configuration import settings
from langroid.utils.output.printing import print_long_text

logger = logging.getLogger(__name__)


class VectorStoreConfig(BaseSettings):
    collection_name: str | None = None
    replace_collection: bool = False  # replace collection if it already exists
    storage_path: str = ".qdrant/data"
    cloud: bool = False
    batch_size: int = 200
    embedding: EmbeddingModelsConfig = OpenAIEmbeddingsConfig(
        model_type="openai",
    )
    timeout: int = 60
    type: str = "qdrant"
    host: str = "127.0.0.1"
    port: int = 6333
    # compose_file: str = "langroid/vector_store/docker-compose-qdrant.yml"


class VectorStore(ABC):
    """
    Abstract base class for a vector store.
    """

    def __init__(self, config: VectorStoreConfig):
        self.config = config

    @staticmethod
    def create(config: VectorStoreConfig) -> "VectorStore":
        from langroid.vector_store.chromadb import ChromaDB
        from langroid.vector_store.qdrantdb import QdrantDB

        vecstore_class = dict(qdrant=QdrantDB, chroma=ChromaDB).get(
            config.type, QdrantDB
        )

        return vecstore_class(config)  # type: ignore

    @abstractmethod
    def clear_empty_collections(self) -> int:
        """Clear all empty collections in the vector store.
        Returns the number of collections deleted.
        """
        pass

    @abstractmethod
    def list_collections(self) -> List[str]:
        """List all collections in the vector store."""
        pass

    def set_collection(self, collection_name: str, replace: bool = False) -> None:
        """
        Set the current collection to the given collection name.
        Args:
            collection_name (str): Name of the collection.
            replace (bool, optional): Whether to replace the collection if it
                already exists. Defaults to False.
        """

        self.config.collection_name = collection_name
        if collection_name not in self.list_collections() or replace:
            self.create_collection(collection_name, replace=replace)

    @abstractmethod
    def create_collection(self, collection_name: str, replace: bool = False) -> None:
        """Create a collection with the given name.
        Args:
            collection_name (str): Name of the collection.
            replace (bool, optional): Whether to replace the
                collection if it already exists. Defaults to False.
        """
        pass

    @abstractmethod
    def add_documents(self, documents: Sequence[Document]) -> None:
        pass

    @abstractmethod
    def similar_texts_with_scores(
        self,
        text: str,
        k: int = 1,
        where: Optional[str] = None,
    ) -> List[Tuple[Document, float]]:
        pass

    @abstractmethod
    def get_documents_by_ids(self, ids: List[str]) -> List[Document]:
        """
        Get documents by their ids.
        Args:
            ids (List[str]): List of document ids.

        Returns:
            List[Document]: List of documents
        """
        pass

    @abstractmethod
    def delete_collection(self, collection_name: str) -> None:
        pass

    def show_if_debug(self, doc_score_pairs: List[Tuple[Document, float]]) -> None:
        if settings.debug:
            for i, (d, s) in enumerate(doc_score_pairs):
                print_long_text("red", "italic red", f"MATCH-{i}", d.content)
