#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
向量语义检索引擎 (rag_vector.py)
=================================
基于 sentence-transformers + FAISS 的语义级法规检索。
作为 rag_engine.py 的增强层，自动降级回 TF-IDF。

用法：
    from core.rag_vector import semantic_retrieve, build_vector_index, get_vector_status
"""

from __future__ import annotations

import os
import pickle
import time
from pathlib import Path
from typing import Dict, List, Optional

# ═══════════════ 配置 ═══════════════

CACHE_DIR = Path(__file__).resolve().parent.parent / "data" / "rag_cache"
VECTOR_CACHE = CACHE_DIR / "vector_faiss.pkl"

EMBEDDING_MODEL = os.environ.get(
    "RAG_EMBEDDING_MODEL",
    "paraphrase-multilingual-MiniLM-L12-v2"  # 384-dim, 118MB, 支持中文
)
LOCAL_MODEL_DIR = Path(__file__).resolve().parent.parent / "data" / "models" / "sentence-transformers" / "paraphrase-multilingual-MiniLM-L12-v2"
TOP_K = 15
MIN_SCORE = 0.35

# 来源权重
SOURCE_WEIGHTS = {
    "01_中注协审计准则体系": 1.5,
    "02_财政部企业会计准则体系": 1.5,
    "03_事务所内部文件": 1.2,
    "04_法律法规": 1.3,
}

# ═══════════════ 全局状态 ═══════════════

_model = None; _index = None; _chunks: List[dict] = []; _dim = 0
_ready = False


def _init_model():
    global _model, _dim
    if _model is not None: return
    from sentence_transformers import SentenceTransformer

    # rag_vector 固定用 MiniLM（向量索引构建：384 维，489 块仅需数秒）
    # Qwen3-0.6B 由 scenario_packs._get_embedder() 独立管理（场景路由 + few-shot）
    if LOCAL_MODEL_DIR.exists():
        print(f"[VECTOR] 从本地加载 MiniLM: {LOCAL_MODEL_DIR}")
        _model = SentenceTransformer(str(LOCAL_MODEL_DIR))
    else:
        print(f"[VECTOR] 在线加载: {EMBEDDING_MODEL}")
        _model = SentenceTransformer(EMBEDDING_MODEL)
    _dim = _model.get_sentence_embedding_dimension() if hasattr(_model, 'get_sentence_embedding_dimension') else _model.get_embedding_dimension()
    print(f"[VECTOR] MiniLM 已加载, dim={_dim}")


def _get_weight(cat: str) -> float:
    for k, v in SOURCE_WEIGHTS.items():
        if cat.startswith(k) or k in cat: return v
    return 1.0


def build_vector_index(force: bool = False):
    """构建向量索引（从知识库分块→嵌入→FAISS索引）"""
    global _model, _index, _chunks, _dim, _ready

    if _ready and not force:
        return len(_chunks)

    # 尝试加载缓存
    if not force and VECTOR_CACHE.exists():
        try:
            with open(VECTOR_CACHE, "rb") as f:
                data = pickle.load(f)
            _chunks = data["chunks"]
            _dim = data["dim"]
            _init_model()
            import numpy as np; import faiss
            emb = np.asarray(data["embeddings"], dtype=np.float32)
            _index = faiss.IndexFlatIP(_dim)
            _index.add(emb)
            _ready = True
            print(f"[VECTOR] 缓存加载: {len(_chunks)} 块")
            return len(_chunks)
        except Exception as e:
            print(f"[VECTOR] 缓存加载失败: {e}")

    # 从 rag_engine 获取分块
    try:
        from core.rag_engine import load_all_documents
    except ImportError:
        print("[VECTOR] 无法导入 rag_engine，跳过")
        return 0

    chunks = load_all_documents()
    if not chunks:
        print("[VECTOR] 知识库为空")
        return 0

    _chunks = chunks
    _init_model()

    texts = [c.get("text", "") for c in chunks]
    print(f"[VECTOR] 正在嵌入 {len(texts)} 个文本块...")
    t0 = time.time()
    embeddings = _model.encode(texts, batch_size=8, show_progress_bar=True, normalize_embeddings=True)
    _dim = embeddings.shape[1]  # 直接从 embedding 结果取维度
    print(f"[VECTOR] 嵌入完成, 耗时 {time.time()-t0:.1f}s, dim={_dim}")

    import numpy as np; import faiss
    _index = faiss.IndexFlatIP(_dim)
    _index.add(np.asarray(embeddings, dtype=np.float32))

    # 缓存
    with open(VECTOR_CACHE, "wb") as f:
        pickle.dump({"chunks": chunks, "embeddings": embeddings, "dim": _dim}, f)
    print(f"[VECTOR] 索引已缓存: {VECTOR_CACHE}")

    _ready = True
    return len(_chunks)


def semantic_retrieve(query: str, top_k: int = TOP_K) -> List[dict]:
    """语义向量检索"""
    if not _ready:
        try:
            build_vector_index()
        except Exception as e:
            print(f"[VECTOR] 向量检索不可用: {e}")
            return []
    if not _ready:
        return []

    q_emb = _model.encode([query], normalize_embeddings=True, show_progress_bar=False)
    scores, indices = _index.search(q_emb, min(top_k * 2, len(_chunks)))

    results = []
    for score, idx in zip(scores[0], indices[0]):
        if idx < 0 or idx >= len(_chunks):
            continue
        chunk = _chunks[idx]
        cat = chunk.get("category", chunk.get("source", "")[:30])
        adj = float(score) * _get_weight(cat)
        if adj < MIN_SCORE: continue
        results.append({
            "score": round(adj, 4), "raw_score": round(float(score), 4),
            "text": chunk["text"], "source": chunk.get("source", ""),
            "category": cat,
        })

    results.sort(key=lambda x: x["score"], reverse=True)
    return results[:top_k]


def get_vector_status() -> dict:
    return {
        "ready": _ready,
        "model": EMBEDDING_MODEL,
        "dim": _dim,
        "chunks": len(_chunks),
        "cache": VECTOR_CACHE.exists(),
    }
