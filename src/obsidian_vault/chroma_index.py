from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

import chromadb
import numpy as np
from chromadb.api.models.Collection import Collection
from chromadb.utils.embedding_functions import DefaultEmbeddingFunction
from chromadb.utils.embedding_functions.onnx_mini_lm_l6_v2 import ONNXMiniLM_L6_V2
from tokenizers import Tokenizer


@dataclass(frozen=True)
class ChromaResult:
    total_chunks: int
    upserted: int
    unchanged: int
    deleted: int


@dataclass(frozen=True)
class ChromaChunk:
    chunk_id: str
    document: str
    metadata: dict[str, str | int]


class DefaultEmbeddingModel:
    """Chroma's local ONNX model plus an untruncated token counter."""

    max_tokens = 256
    token_counter_id = "all-MiniLM-L6-v2:tokenizer-v1"

    def __init__(self) -> None:
        self.embedding_function = DefaultEmbeddingFunction()
        self._tokenizer: Tokenizer | None = None
        self._onnx: ONNXMiniLM_L6_V2 | None = None

    def prepare(self) -> None:
        if self._tokenizer is not None:
            return
        tokenizer_path = (
            ONNXMiniLM_L6_V2.DOWNLOAD_PATH
            / ONNXMiniLM_L6_V2.EXTRACTED_FOLDER_NAME
            / "tokenizer.json"
        )
        if not tokenizer_path.is_file():
            # Let Chroma download and verify its model on the first run.
            self.embed(["Initialize the local embedding model."])
        self._tokenizer = Tokenizer.from_file(str(tokenizer_path))
        self._tokenizer.no_truncation()
        self._tokenizer.no_padding()

    def count_tokens(self, text: str) -> int:
        if self._tokenizer is None:
            self.prepare()
        assert self._tokenizer is not None
        return len(self._tokenizer.encode(text).ids)

    def embed(self, documents: list[str]) -> list[np.ndarray[Any, Any]]:
        if self._onnx is None:
            self._onnx = ONNXMiniLM_L6_V2()
        return list(self._onnx(documents))


def _batches[T](values: list[T], size: int) -> Iterator[list[T]]:
    for start in range(0, len(values), size):
        yield values[start : start + size]


def sqlite_chunks(database_path: Path) -> list[ChromaChunk]:
    with sqlite3.connect(database_path) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            """
            SELECT c.chunk_id, c.file_id, c.heading_path, c.section_index,
                   c.part_index, c.embedding_text, c.content_hash,
                   c.word_count, c.token_count, n.source_path
              FROM chunks AS c
              JOIN notes AS n USING(file_id)
             ORDER BY c.chunk_id
            """
        ).fetchall()
    return [
        ChromaChunk(
            chunk_id=row["chunk_id"],
            document=row["embedding_text"],
            metadata={
                "file_id": row["file_id"],
                "source_path": row["source_path"],
                "heading_path": row["heading_path"],
                "section_index": row["section_index"],
                "part_index": row["part_index"],
                "content_hash": row["content_hash"],
                "word_count": row["word_count"],
                "token_count": row["token_count"],
            },
        )
        for row in rows
    ]


class ChromaIndex:
    def __init__(
        self,
        path: Path,
        collection_name: str,
        model: DefaultEmbeddingModel,
    ) -> None:
        self.client = chromadb.PersistentClient(path=str(path))
        self.model = model
        self.collection = self.client.get_or_create_collection(
            name=collection_name,
            embedding_function=model.embedding_function,
            metadata={"embedding_model": "all-MiniLM-L6-v2"},
        )

    def sync(self, database_path: Path, batch_size: int = 128) -> ChromaResult:
        desired = sqlite_chunks(database_path)
        existing_result = self.collection.get(include=["metadatas"])
        existing_metadata = existing_result["metadatas"] or []
        existing = {
            chunk_id: metadata or {}
            for chunk_id, metadata in zip(existing_result["ids"], existing_metadata)
        }
        desired_ids = {chunk.chunk_id for chunk in desired}
        stale_ids = sorted(set(existing) - desired_ids)
        changed = [
            chunk
            for chunk in desired
            if existing.get(chunk.chunk_id, {}).get("content_hash")
            != chunk.metadata["content_hash"]
        ]
        metadata_only = [
            chunk
            for chunk in desired
            if existing.get(chunk.chunk_id, {}).get("content_hash")
            == chunk.metadata["content_hash"]
            and existing[chunk.chunk_id] != chunk.metadata
        ]
        print(
            f"Compared {len(desired)} chunks: {len(changed)} need embeddings, "
            f"{len(metadata_only)} need metadata updates, {len(stale_ids)} stale.",
            flush=True,
        )

        for batch in _batches(stale_ids, batch_size):
            self.collection.delete(ids=batch)
        for batch in _batches(metadata_only, batch_size):
            self.collection.update(
                ids=[chunk.chunk_id for chunk in batch],
                metadatas=[chunk.metadata for chunk in batch],
            )
        completed = 0
        if changed:
            print(f"Embedding {len(changed)} chunks (loading model if needed)...", flush=True)
        for batch in _batches(changed, batch_size):
            documents = [chunk.document for chunk in batch]
            self.collection.upsert(
                ids=[chunk.chunk_id for chunk in batch],
                embeddings=self.model.embed(documents),
                documents=documents,
                metadatas=[chunk.metadata for chunk in batch],
            )
            completed += len(batch)
            print(f"Embedded {completed}/{len(changed)} changed chunks.", flush=True)

        return ChromaResult(
            total_chunks=len(desired),
            upserted=len(changed),
            unchanged=len(desired) - len(changed),
            deleted=len(stale_ids),
        )


def query_collection(
    path: Path,
    collection_name: str,
    query: str,
    results: int,
    model: DefaultEmbeddingModel,
    *,
    where: dict[str, Any] | None = None,
) -> dict[str, Any]:
    client = chromadb.PersistentClient(path=str(path))
    collection: Collection = client.get_collection(
        collection_name, embedding_function=model.embedding_function
    )
    query_embedding = model.embed([query])
    return collection.query(
        query_embeddings=query_embedding,
        where=where,
        n_results=results,
        include=["documents", "metadatas", "distances"],
    )
