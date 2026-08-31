import threading
import time
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from importlib.metadata import version
from typing import Optional

import requests

from dm_sdk.tools.dm_static_env import (
    _KAFKA_TRACK_TOPIC,
    get_cerberus_host,
)
from dm_sdk.tools.kafka_producer_pool import get_producer


@dataclass
class _TrackAction:
    data_id: str
    data_source: str
    table: str
    func_name: str


@dataclass
class _TrackContext:
    sdk_version: str


@dataclass
class TrackEvent:
    event_id: str
    event_time: int
    user_name: str
    action: _TrackAction
    context: _TrackContext


class Tracker:
    def __init__(
        self, access_token: str, data_source: str, env: str
    ):
        self._data_source = data_source
        self._access_token = access_token
        self._env = env
        self._topic = _KAFKA_TRACK_TOPIC
        self._sdk_version = version("dm_sdk")
        self._producer: Optional = None
        self._user_name: str = ""

        threading.Thread(target=self._resolve_user_name, daemon=True).start()

    def _ensure_producer(self) -> None:
        """懒加载：首次调用时从池中获取共享 KafkaProducer。"""
        if self._producer is not None:
            return
        producer = get_producer(self._env)
        if producer is not None:
            self._producer = producer

    def _resolve_user_name(self) -> None:
        for delay in (0, 5, 10):
            time.sleep(delay)
            try:
                resp = requests.get(
                    get_cerberus_host(self._env)
                    + "/permissionCache/getUserNameByAccessToken",
                    headers={"Access-Token": self._access_token},
                    timeout=5,
                )
                resp.raise_for_status()
                self._user_name = resp.json().get("data", "")
                return
            except Exception:
                pass

    def track(self, data_id: str, table: str, func_name: str) -> None:
        if not data_id:
            return

        self._ensure_producer()
        producer = self._producer
        if producer is None:
            return

        try:
            event = TrackEvent(
                event_id=str(uuid.uuid4()),
                event_time=int(datetime.now(timezone.utc).timestamp() * 1000),
                user_name=self._user_name or "",
                action=_TrackAction(
                    data_id=data_id,
                    data_source=self._data_source,
                    table=table,
                    func_name=func_name,
                ),
                context=_TrackContext(sdk_version=self._sdk_version),
            )
            producer.send(self._topic, asdict(event))
        except Exception:
            pass
