"""向量语义路由 — 用 MiniLM + ChromaDB 替代关键词子串匹配

将 templates.jsonl 和用户策略的 nl/keywords 编码为向量存入 ChromaDB。
用户查询时编码为向量，top-k 检索最相似的 recipe。
"""
import os
import logging
from typing import List, Optional, Tuple

logger = logging.getLogger(__name__)

# ── 延迟导入，避免 chromadb/sentence-transformers 在 import 时触发大模型下载 ──
_chromadb = None
_collection = None
_embedding_model = None
_LOADED = False

COLLECTION_NAME = "scene_sql_recipes"
# 优先使用 BGE-M3（多语言更优），若本地不存在则 fallback 到 MiniLM
_LOCAL_BGE_M3 = "/root/models/bge-m3"  # DSW上hfd.sh下载的本地路径
EMBED_MODEL_CANDIDATES = [
    os.environ.get("SCENESQL_EMBED_MODEL", ""),  # 环境变量覆盖
    _LOCAL_BGE_M3 if os.path.isdir(_LOCAL_BGE_M3) else "",  # 本地BGE-M3（优先，免下载）
    "BAAI/bge-m3",          # 2.2GB, 多语言SOTA, 需GPU/CPU 1.5GB+ RAM
    "all-MiniLM-L6-v2",     # 80MB, CPU-friendly, multilingual baseline
]
EMBED_MODEL = None  # 在 _ensure_loaded() 中确定


def _ensure_loaded():
    """懒加载 ChromaDB 和 embedding 模型。首次调用时下载/加载。"""
    global _chromadb, _collection, _embedding_model, _LOADED, EMBED_MODEL
    if _LOADED:
        return

    try:
        import chromadb
        _chromadb = chromadb
    except ImportError:
        logger.warning("chromadb not installed, vector routing disabled")
        return

    try:
        from sentence_transformers import SentenceTransformer
        # 选择可用的 embedding 模型
        for candidate in EMBED_MODEL_CANDIDATES:
            if not candidate:
                continue
            try:
                _embedding_model = SentenceTransformer(candidate)
                EMBED_MODEL = candidate
                logger.info(f"Loaded embedding model: {candidate}")
                break
            except Exception as e:
                logger.debug(f"Model '{candidate}' not available: {e}")
                continue
        if _embedding_model is None:
            logger.warning("No embedding model available, vector routing disabled")
            return
    except ImportError:
        logger.warning("sentence-transformers not installed, vector routing disabled")
        return

    # ChromaDB 持久化目录 — 根据模型选择不同DB（BGE-M3和MiniLM维度不同，不能混用）
    if "bge-m3" in (EMBED_MODEL or "").lower():
        db_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "vector_db_bge_m3")
    else:
        db_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "vector_db")
    client = chromadb.PersistentClient(path=db_dir)
    _collection = client.get_or_create_collection(
        name=COLLECTION_NAME,
        metadata={"hnsw:space": "cosine"},
    )
    _LOADED = True
    logger.info(f"ChromaDB loaded, collection '{COLLECTION_NAME}' has {_collection.count()} entries")


def is_available() -> bool:
    """检查向量路由是否可用。"""
    _ensure_loaded()
    return _LOADED and _collection is not None and _embedding_model is not None


def index_recipes(entries: List[Tuple[str, str, str]]):
    """批量索引 recipe 条目。

    Args:
        entries: [(id, text_for_embedding, recipe_name), ...]
            text_for_embedding = nl 描述 + keywords 拼接
            recipe_name = recipe YAML name (用于 ConceptRouter 映射)
    """
    if not is_available():
        return

    # 去重：ChromaDB 不支持 upsert 同 ID，先删除再添加
    ids = [e[0] for e in entries]
    existing = set(_collection.get(ids=ids)["ids"]) if _collection.count() > 0 else set()
    if existing:
        _collection.delete(ids=list(existing))

    texts = [e[1] for e in entries]
    metadatas = [{"recipe_name": e[2]} for e in entries]

    # 编码
    embeddings = _embedding_model.encode(texts, show_progress_bar=False).tolist()

    _collection.add(
        ids=ids,
        embeddings=embeddings,
        documents=texts,
        metadatas=metadatas,
    )
    logger.info(f"Indexed {len(entries)} recipe entries into ChromaDB")


def search(query: str, top_k: int = 3) -> List[Tuple[str, float]]:
    """向量搜索：返回 [(recipe_name, similarity_score), ...]

    Args:
        query: 用户输入的自然语言
        top_k: 返回前 k 个结果

    Returns:
        [(recipe_name, distance), ...]  distance 越小越相似（cosine distance）
    """
    if not is_available() or _collection.count() == 0:
        return []

    query_embedding = _embedding_model.encode([query], show_progress_bar=False).tolist()
    results = _collection.query(
        query_embeddings=query_embedding,
        n_results=min(top_k, _collection.count()),
        include=["metadatas", "distances"],
    )

    if not results["ids"][0]:
        return []

    return [
        (results["metadatas"][0][i]["recipe_name"], results["distances"][0][i])
        for i in range(len(results["ids"][0]))
    ]


def load_from_templates():
    """从 templates.jsonl + 用户策略 加载所有 recipe 并索引。"""
    entries = []
    _id_counter = 0

    # 1. 系统模板
    templates_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "templates.jsonl",
    )
    if os.path.exists(templates_path):
        import json
        with open(templates_path) as f:
            for line in f:
                t = json.loads(line)
                _id_counter += 1
                text = f"{t.get('nl', '')} {t.get('domain', '')}"
                entries.append((f"t_{_id_counter}", text, t.get("id", "")))

    # 2. 用户策略
    strategy_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "user_strategies")
    if os.path.isdir(strategy_dir):
        import yaml
        for p in sorted(os.listdir(strategy_dir)):
            if not p.endswith(".yaml"):
                continue
            try:
                with open(os.path.join(strategy_dir, p)) as f:
                    d = yaml.safe_load(f)
                _id_counter += 1
                keywords = " ".join(d.get("keywords", []))
                desc = d.get("description", "")
                text = f"{keywords} {desc}"
                entries.append((f"u_{_id_counter}", text, d.get("name", p.replace(".yaml", ""))))
            except Exception as e:
                logger.warning(f"Failed to load strategy {p}: {e}")

    if entries:
        index_recipes(entries)


def clear():
    """清空向量索引（用于重新索引）。"""
    global _collection, _LOADED
    if _collection is not None:
        try:
            _chromadb_client = _collection._client
            _chromadb_client.delete_collection(COLLECTION_NAME)
        except Exception:
            pass
        _collection = None
        _LOADED = False
