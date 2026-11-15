#!/usr/bin/env python3
"""Offline restored-index checks; run in a network-disabled container.

Uses retained embeddings, never constructs an embedding model or calls a provider.
RAG source/runtime must be mounted read-only; data must be a private restored copy.
"""
import collections
import json
import os
import sqlite3
import sys
from pathlib import Path

os.environ['ANONYMIZED_TELEMETRY'] = 'False'
sys.dont_write_bytecode = True
from recovery_guard import require_disposable_clone

root = require_disposable_clone(os.environ['RAG_DATA_DIR'], ('rag.db', 'chroma', 'bm25'))
sys.path.insert(0, os.environ['RAG_SOURCE'])
import chromadb
from chromadb.config import Settings
from storage.bm25_store import BM25Store

registry = sqlite3.connect((root / 'rag.db').as_uri() + '?mode=ro', uri=True)
registry.row_factory = sqlite3.Row
client = chromadb.PersistentClient(path=str(root / 'chroma'), settings=Settings(anonymized_telemetry=False))
lexical = BM25Store(str(root / 'bm25'))
found = collections.Counter()
summary = {'collections': 0, 'vector_queries': 0, 'embedding_rows': 0,
           'bm25_mismatches': 0, 'ready_document_mismatches': 0, 'source_files_missing': 0,
           'embedding_model_calls': 0, 'external_network': 'caller-enforced isolation required'}
for listed in client.list_collections():
    collection = client.get_collection(listed.name, embedding_function=None)
    rows = collection.get(include=['embeddings', 'metadatas'])
    ids, corpus, _ = lexical._load(listed.name)
    summary['collections'] += 1
    summary['embedding_rows'] += len(rows['ids'])
    summary['bm25_mismatches'] += int(set(ids) != set(rows['ids']))
    for metadata in rows['metadatas']:
        found[metadata.get('document_id')] += 1
    if rows['ids']:
        answer = collection.query(query_embeddings=[rows['embeddings'][0].tolist()], n_results=1)
        assert len(answer['ids'][0]) == 1
        summary['vector_queries'] += 1
for document in registry.execute("SELECT * FROM documents WHERE ingest_status='ready'"):
    summary['ready_document_mismatches'] += int(found[document['id']] != document['chunk_count'])
    if document['source_path']:
        summary['source_files_missing'] += int(not Path(document['source_path']).is_file())
summary['result'] = 'pass' if not any(summary[key] for key in
    ['bm25_mismatches', 'ready_document_mismatches', 'source_files_missing']) else 'needs_reconciliation'
print(json.dumps(summary))
raise SystemExit(0 if summary['result'] == 'pass' else 1)
