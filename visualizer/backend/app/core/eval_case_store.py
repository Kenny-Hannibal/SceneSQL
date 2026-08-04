"""评测 case 标注存储 — 每个策略一个 JSONL 文件

存放于 agent/backend/app/core/eval_cases/<策略名>.jsonl（与 user_strategies/ 平级）。
行格式：{bag_id, start_ts, end_ts, verdict, labeled_at, labeled_by}
时间戳存秒（与查询结果行一致），同步到产线时才转纳秒。
"""
import json
import logging
import os
import threading
import time
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

_EVAL_CASES_DIR = os.path.normpath(os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "..", "..", "..", "..",
    "agent", "backend", "app", "core", "eval_cases"
))


def _safe_name(strategy_name: str) -> str:
    return strategy_name.replace("/", "_").replace("\\", "_")


class EvalCaseStore:
    def __init__(self, cases_dir: str = _EVAL_CASES_DIR):
        self._dir = cases_dir
        self._lock = threading.Lock()

    def _path(self, strategy_name: str) -> str:
        return os.path.join(self._dir, f"{_safe_name(strategy_name)}.jsonl")

    def _read(self, strategy_name: str) -> List[Dict]:
        path = self._path(strategy_name)
        if not os.path.exists(path):
            return []
        cases = []
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    cases.append(json.loads(line))
                except json.JSONDecodeError:
                    logger.warning("跳过无效标注行: %s", line[:80])
        return cases

    def _write(self, strategy_name: str, cases: List[Dict]) -> None:
        os.makedirs(self._dir, exist_ok=True)
        path = self._path(strategy_name)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            for c in cases:
                f.write(json.dumps(c, ensure_ascii=False) + "\n")
        os.replace(tmp, path)

    def add_case(
        self,
        strategy_name: str,
        bag_id: str,
        start_ts: Optional[int],
        end_ts: Optional[int],
        verdict: str,
        labeled_by: str = "",
    ) -> Dict:
        """按 (bag_id, start_ts, end_ts) upsert，重复标注翻转 verdict。"""
        with self._lock:
            cases = self._read(strategy_name)
            case = {
                "bag_id": bag_id,
                "start_ts": start_ts,
                "end_ts": end_ts,
                "verdict": verdict,
                "labeled_at": time.time(),
                "labeled_by": labeled_by,
            }
            replaced = False
            for i, c in enumerate(cases):
                if c.get("bag_id") == bag_id and c.get("start_ts") == start_ts and c.get("end_ts") == end_ts:
                    cases[i] = case
                    replaced = True
                    break
            if not replaced:
                cases.append(case)
            self._write(strategy_name, cases)
            return case

    def remove_case(
        self,
        strategy_name: str,
        bag_id: str,
        start_ts: Optional[int],
        end_ts: Optional[int],
    ) -> bool:
        with self._lock:
            cases = self._read(strategy_name)
            new = [
                c for c in cases
                if not (c.get("bag_id") == bag_id and c.get("start_ts") == start_ts and c.get("end_ts") == end_ts)
            ]
            if len(new) == len(cases):
                return False
            self._write(strategy_name, new)
            return True

    def list_cases(self, strategy_name: str) -> List[Dict]:
        return self._read(strategy_name)

    def clear(self, strategy_name: str) -> None:
        """删除策略时级联清理标注文件。"""
        with self._lock:
            path = self._path(strategy_name)
            if os.path.exists(path):
                os.remove(path)


eval_case_store = EvalCaseStore()
