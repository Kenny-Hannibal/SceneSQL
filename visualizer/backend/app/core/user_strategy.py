"""User Strategy CRUD — 用户自定义SQL策略（自定义Recipe）

存储格式与 system recipes 相同的 YAML，存放于 user_strategies/ 目录。
策略在 ConceptRouter 中优先于系统 recipe。
"""
import os
import yaml
import logging
import time
from pathlib import Path
from typing import List, Optional
from pydantic import BaseModel

logger = logging.getLogger(__name__)

# 默认存储路径：与 recipes/ 同级
DEFAULT_STRATEGY_DIR = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "user_strategies"
)


class StrategyCreateRequest(BaseModel):
    name: str
    keywords: List[str]       # 用于 ConceptRouter 匹配的关键词
    tag_name: str             # SQL 中的 tag_name
    sql: str                  # SQL 文本
    description: str = ""


class StrategyUpdateRequest(BaseModel):
    keywords: Optional[List[str]] = None
    tag_name: Optional[str] = None
    sql: Optional[str] = None
    description: Optional[str] = None


class StrategyInfo(BaseModel):
    name: str
    keywords: List[str]
    tag_name: str
    sql: str
    description: str
    created_at: Optional[float] = None
    updated_at: Optional[float] = None


class UserStrategyManager:
    """管理用户自定义策略的 CRUD 操作。"""

    def __init__(self, strategy_dir: str = DEFAULT_STRATEGY_DIR):
        self.strategy_dir = Path(strategy_dir)
        self.strategy_dir.mkdir(parents=True, exist_ok=True)

    def _yaml_path(self, name: str) -> Path:
        # 安全性：只允许字母数字下划线
        safe_name = "".join(c for c in name if c.isalnum() or c in ("_", "-"))
        if not safe_name:
            raise ValueError(f"Invalid strategy name: {name}")
        return self.strategy_dir / f"{safe_name}.yaml"

    def list_strategies(self) -> List[StrategyInfo]:
        """列出所有用户策略。"""
        result = []
        for p in sorted(self.strategy_dir.glob("*.yaml")):
            try:
                with open(p) as f:
                    d = yaml.safe_load(f)
                result.append(StrategyInfo(
                    name=d.get("name", p.stem),
                    keywords=d.get("keywords", []),
                    tag_name=d.get("variants", {}).get("default", {}).get("tag_name", ""),
                    sql=d.get("variants", {}).get("default", {}).get("raw_sql", ""),
                    description=d.get("description", ""),
                    created_at=d.get("created_at"),
                    updated_at=d.get("updated_at"),
                ))
            except Exception as e:
                logger.warning(f"Failed to load strategy {p}: {e}")
        return result

    def get_strategy(self, name: str) -> StrategyInfo:
        """获取单个策略。"""
        p = self._yaml_path(name)
        if not p.exists():
            raise FileNotFoundError(f"Strategy not found: {name}")
        with open(p) as f:
            d = yaml.safe_load(f)
        return StrategyInfo(
            name=d.get("name", name),
            keywords=d.get("keywords", []),
            tag_name=d.get("variants", {}).get("default", {}).get("tag_name", ""),
            sql=d.get("variants", {}).get("default", {}).get("raw_sql", ""),
            description=d.get("description", ""),
            created_at=d.get("created_at"),
            updated_at=d.get("updated_at"),
        )

    def create_strategy(self, req: StrategyCreateRequest) -> StrategyInfo:
        """创建新策略。"""
        p = self._yaml_path(req.name)
        if p.exists():
            raise FileExistsError(f"Strategy already exists: {req.name}")

        now = time.time()
        doc = {
            "name": req.name,
            "version": "1.0",
            "description": req.description,
            "keywords": req.keywords,
            "created_at": now,
            "updated_at": now,
            "blocks": [],
            "variants": {
                "default": {
                    "tag_name": req.tag_name,
                    "raw_sql": req.sql,
                }
            },
        }
        with open(p, "w") as f:
            yaml.dump(doc, f, allow_unicode=True, default_flow_style=False)

        logger.info(f"User strategy created: {req.name} (keywords={req.keywords})")
        return self.get_strategy(req.name)

    def update_strategy(self, name: str, req: StrategyUpdateRequest) -> StrategyInfo:
        """更新策略。"""
        p = self._yaml_path(name)
        if not p.exists():
            raise FileNotFoundError(f"Strategy not found: {name}")

        with open(p) as f:
            d = yaml.safe_load(f)

        if req.keywords is not None:
            d["keywords"] = req.keywords
        if req.description is not None:
            d["description"] = req.description
        if req.tag_name is not None:
            d["variants"]["default"]["tag_name"] = req.tag_name
        if req.sql is not None:
            d["variants"]["default"]["raw_sql"] = req.sql
        d["updated_at"] = time.time()

        with open(p, "w") as f:
            yaml.dump(d, f, allow_unicode=True, default_flow_style=False)

        logger.info(f"User strategy updated: {name}")
        return self.get_strategy(name)

    def delete_strategy(self, name: str) -> bool:
        """删除策略。"""
        p = self._yaml_path(name)
        if not p.exists():
            raise FileNotFoundError(f"Strategy not found: {name}")
        p.unlink()
        logger.info(f"User strategy deleted: {name}")
        return True

    def get_concept_recipe_map_entries(self) -> dict:
        """返回可供 ConceptRouter 注入的 {keyword: (recipe_name, variant)} 映射。"""
        entries = {}
        for info in self.list_strategies():
            for kw in info.keywords:
                entries[kw] = (info.name, "default")
        return entries
