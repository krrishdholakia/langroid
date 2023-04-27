from abc import ABC, abstractmethod
from llmagent.utils.output.printing import print_long_text
from llmagent.utils.logging import setup_logger
from llmagent.utils.configuration import settings
import logging
logger = logging.getLogger(__name__)
#logger = setup_logger(__name__, logging.NOTSET)


class VectorStore(ABC):
    def __init__(self, collection_name):
        self.collection_name = collection_name

    @classmethod
    @abstractmethod
    def from_documents(cls, collection_name, documents, embeddings=None,
                       metadatas=None, ids=None):
        pass

    @abstractmethod
    def add_documents(self, embeddings=None, documents=None,
                      metadatas=None, ids=None):
        pass

    @abstractmethod
    def similar_texts_with_scores(self, text:str, k:int=None,
                                  where:str=None, debug:bool=False):
        pass

    def show_if_debug(self, doc_score_pairs):
        #if logger.isEnabledFor(logging.DEBUG):
        if settings.debug:
            for i, (d, s) in enumerate(doc_score_pairs):
                print_long_text("red", "italic red", f"MATCH-{i}", d.content)
