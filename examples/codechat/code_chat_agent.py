from typing import List
from examples.urlqa.doc_chat_agent import DocChatAgent, DocChatAgentConfig
from llmagent.parsing.repo_loader import RepoLoader, RepoLoaderConfig
from llmagent.vector_store.qdrantdb import QdrantDBConfig
from llmagent.embedding_models.models import OpenAIEmbeddingsConfig
from llmagent.vector_store.base import VectorStoreConfig, VectorStore
from llmagent.language_models.openai_gpt import OpenAIGPTConfig, OpenAIChatModel
from llmagent.parsing.parser import ParsingConfig, Splitter
from llmagent.parsing.code_parser import CodeParsingConfig
from llmagent.prompts.prompts_config import PromptsConfig
from llmagent.prompts.templates import ANSWER_PROMPT_USE_HISTORY_GPT4
from llmagent.mytypes import Document, DocMetaData
from rich.prompt import Prompt
from pathlib import Path
from llmagent.parsing.urls import org_user_from_github


from examples.codechat.code_chat_tools import (
    ShowFileContentsMessage,
    ShowDirContentsMessage,
)
from examples.dockerchat.dockerchat_agent_messages import RunPythonMessage
import logging
import os
from rich import print
from rich.console import Console

logger = logging.getLogger(__name__)
console = Console()


DEFAULT_CODE_CHAT_INSTRUCTIONS = """
Your task is to answer questions about a code repository. You will be given directlory 
listings, text and code from various files in the repository. You must answer based 
on the information given to you. If you are asked to see if there is a certain file 
in the repository, and it does not occur in the listings you are shown, then you can 
simply answer "No".
"""

DEFAULT_CODE_CHAT_SYSTEM_MESSAGE = """
You are an expert software engineer, helping me understand a code repository.
"""


class CodeChatAgentConfig(DocChatAgentConfig):
    system_message: str = DEFAULT_CODE_CHAT_SYSTEM_MESSAGE
    user_message: str = DEFAULT_CODE_CHAT_INSTRUCTIONS
    # threshold to decide whether to extract relevant parts
    summarize_prompt: str = ANSWER_PROMPT_USE_HISTORY_GPT4
    max_context_tokens: int = 500
    conversation_mode: bool = True
    content_includes: List[str] = [
        "txt",
        "md",
        "yml",
        "yaml",
        "sh",
        "Makefile",
        "ini",
        "toml",
        "json",
        "py",
    ]
    content_excludes: List[str] = []
    repo_url: str = ""
    gpt4: bool = False
    cache: bool = True
    debug: bool = False
    stream: bool = True  # allow streaming where needed
    vecdb: VectorStoreConfig = QdrantDBConfig(
        type="qdrant",
        collection_name="llmagent-codechat",
        storage_path=".qdrant/codechat/",
        embedding=OpenAIEmbeddingsConfig(
            model_type="openai",
            model_name="text-embedding-ada-002",
            dims=1536,
        ),
    )

    llm: OpenAIGPTConfig = OpenAIGPTConfig(
        chat_model=OpenAIChatModel.GPT3_5_TURBO,
        use_chat_for_completion=True,
    )
    parsing: ParsingConfig = ParsingConfig(
        splitter=Splitter.TOKENS,
        chunk_size=100,
    )

    code_parsing: CodeParsingConfig = CodeParsingConfig(
        chunk_size=200,
        token_encoding_model="text-embedding-ada-002",
        extensions=["py", "yml", "yaml", "sh", "md", "txt"],
        n_similar_docs=2,
    )

    prompts: PromptsConfig = PromptsConfig(
        max_tokens=1000,
    )


class CodeChatAgent(DocChatAgent):
    """
    Agent for chatting with a code repository, that uses retrieval-augmented queries
    to answer questions about code-base in 1 turn:
    user question =>
        user prompt = (relevant code extracts + question)
         => LLM answer
    """

    def __init__(self, config: CodeChatAgentConfig):
        # do not allow vecdb creation until we get unique collection name based
        # on the repo url or path.
        config.vecdb.collection_name = None
        super().__init__(config)
        self.original_docs: List[Document] = None
        self.repo_info_message = ""
        if config.repo_url:
            self.ingest_url(config.repo_url)

    def ingest_url(self, url: str) -> None:
        """
        Clone, chunk, ingest contents of a code repo at `url`, into the vector store.

        Args:
            url (str): github URL of repo to ingest
        """
        self._repo_url = url

        default_collection_name = org_user_from_github(self._repo_url)
        collection_name = Prompt.ask(
            f"""Creating a vector-store for contents of {self._repo_url}.
            IMPORTANT: we need a unique collection name for this repo.
            What would you like to name this collection?""",
            default=default_collection_name,
        )
        # so far there is no vector store, so we set the vecdb config to
        # have this collection_name, and then instantiate the vecdb.
        self.config.vecdb.collection_name = collection_name
        self.vecdb = VectorStore.create(self.config.vecdb)

        self.repo_loader = RepoLoader(
            self._repo_url, RepoLoaderConfig(file_types=self.config.content_includes)
        )
        self.repo_path = self.repo_loader.clone()
        # get the repo tree to depth d, with first k lines of each file
        self.repo_tree, _ = self.repo_loader.load(depth=1, lines=100)
        repo_listing_shown = "\n".join(self.repo_loader.ls(self.repo_tree, depth=1))
        self.repo_listing = self.repo_loader.ls(self.repo_tree, depth=2)

        self.repo_info_message = f"""
        Here is some information about the code repository that you can use, 
        in the subsequent questions. For any future questions, you can refer back to 
        this info if needed.
        
        Here is a listing of the files and directories at the root of the repo:
        {repo_listing_shown}
        """

        self.add_user_message(self.repo_info_message)

        dct, documents = self.repo_loader.load(depth=2, lines=100)
        listing = (
            [
                """
                          List of ALL files and directories in this project:
                          If a file is not in this list, then we can be sure that
                          it is not in the repo!
                          """
            ]
            + RepoLoader.ls(dct, depth=1)
        )
        listing = Document(
            content="\n".join(listing),
            metadata=DocMetaData(source="repo_listing"),
        )

        code_docs = [
            doc
            for doc in documents
            if doc.metadata.language
            not in (["md", "txt"] + self.config.content_excludes)
        ] + [listing]

        text_docs = [doc for doc in documents if doc.metadata.language in ["md", "txt"]]

        with console.status("Processing code repo..."):
            self.config.parsing = self.config.parsing
            n_text_splits = self.ingest_docs(text_docs)
            self.config.parsing = self.config.code_parsing
            n_code_splits = self.ingest_docs(code_docs)

        os.environ["TOKENIZERS_PARALLELISM"] = "false"

        print(
            f"""
        [green]I have processed {len(documents)} files from the following GitHub Repo into 
        {n_text_splits} text chunks and {n_code_splits} code chunks:
        {self._repo_url}
        """.strip()
        )

    def show_file_contents(self, msg: ShowFileContentsMessage) -> str:
        """
        Show the contents of a file in the repo.
        """
        filepath = msg.filepath
        try:
            docs = RepoLoader.get_documents(
                path=self.repo_path,
                file_types=[filepath, os.path.basename(filepath)],
                depth=3,
                lines=100,  # show max 100 lines to keep prompt small
            )
            contents = docs[0].content
            return f"""
            Contents of {filepath}:
            {contents}
            """
        except Exception as e:
            logger.error(f"Error loading file {filepath}: {e}")
            return

    def show_dir_contents(self, msg: ShowDirContentsMessage) -> str:
        # msg.dir is a relative path from the root of the repo,
        # so we need to join it with the repo path
        listing = RepoLoader.list_files(
            str(Path(self.repo_path) / msg.dirpath.strip().lstrip("/")),
            depth=1,
            include_types=self.config.content_includes,
            exclude_types=self.config.content_excludes,
        )
        contents = ", ".join(listing)
        return f"""
        Contents of {msg.dirpath}:
        {contents}
        """

    def run_python(self, msg: RunPythonMessage) -> str:
        # TODO: to be implemented. Return dummy msg for now
        logger.error(
            f"""
        This is a placeholder for the run_python method.
        Here is the code:
        {msg.code}
        """
        )
        return "No results, please continue asking your questions."
