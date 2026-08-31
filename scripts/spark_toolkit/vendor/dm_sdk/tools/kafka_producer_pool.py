import json
import logging
import threading
from typing import Dict, Optional, Set

from kafka import KafkaProducer

from dm_sdk.tools.dm_static_env import get_kafka_bootstrap_servers

logging.getLogger("kafka").setLevel(logging.CRITICAL)

_lock = threading.Lock()
_producers: Dict[str, Optional[KafkaProducer]] = {}
_connecting: Set[str] = set()


def _connect(env: str, key: str) -> None:
    """后台线程：尝试连接 Kafka，成功则缓存 producer，失败则缓存 None。"""
    try:
        try:
            producer = KafkaProducer(
                bootstrap_servers=get_kafka_bootstrap_servers(env),
                value_serializer=lambda v: json.dumps(
                    v, ensure_ascii=False
                ).encode("utf-8"),
                max_block_ms=0,
            )
            _producers[key] = producer
        except Exception:
            _producers[key] = None
    finally:
        with _lock:
            _connecting.discard(key)


def get_producer(env: str) -> Optional[KafkaProducer]:
    """按 env 获取共享的 KafkaProducer 实例（非阻塞）。

    同一个 env 只会创建一个 KafkaProducer，多个 Tracker 复用同一连接。
    首次调用时在后台线程发起连接，立即返回 None。
    连接成功后，后续调用返回 producer 实例；失败则不再重试。

    Args:
        env: 环境名称 (dev, uat, prod)。

    Returns:
        KafkaProducer 实例，尚未连接或连接失败时返回 None。
    """
    key = env
    if key in _producers:
        return _producers[key]
    with _lock:
        if key in _connecting:
            return None
        _connecting.add(key)
    threading.Thread(target=_connect, args=(env, key), daemon=True).start()
    return None
