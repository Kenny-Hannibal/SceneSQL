# your_sdk/query_builder.py

from typing import Any, Dict, List, Optional

from dm_sdk.tools.api import ApiBaseError


def field_condition(field: str, operator: str, value: Any) -> Dict[str, Any]:
    """
    构建字段条件（叶子节点）

    示例:
        field_condition("status", "=", "paid")
    """
    if not field or not isinstance(field, str):
        raise ApiBaseError(f"field必须为非空字符串 (field={field!r})")
    return {"field": field, "operator": operator, "value": value}


def _logic_group(logic: str, *conditions: Dict) -> Dict[str, Any]:
    if logic not in ("and", "or", "not"):
        raise ApiBaseError(
            f"logic参数无效: {logic!r}，必须是 'and'、'or' 或 'not'"
        )
    if not conditions:
        raise ApiBaseError(f"{logic}逻辑组合至少需要1个条件")
    return {"logic": logic, "conditions": list(conditions)}


def and_(*conditions: Dict) -> Dict[str, Any]:
    """AND 逻辑组合"""
    return _logic_group("and", *conditions)


def or_(*conditions: Dict) -> Dict[str, Any]:
    """OR 逻辑组合"""
    return _logic_group("or", *conditions)


def not_(*conditions: Dict) -> Dict[str, Any]:
    """
    NOT 逻辑组合（支持多个条件，表示 NOT (A AND B ...)）
    如果后端要求只能一个条件，可取消注释下方校验
    """
    # if len(conditions) != 1:
    #     raise InvalidConditionError("NOT should contain exactly one condition")
    return _logic_group("not", *conditions)


# ===== 操作符映射 =====
def _eq(field: str, value: Any) -> Dict[str, Any]:
    return {"field": field, "operator": "==", "value": value}


def _neq(field: str, value: Any) -> Dict[str, Any]:
    return {"field": field, "operator": "!=", "value": value}


def _gt(field: str, value: Any) -> Dict[str, Any]:
    return {"field": field, "operator": ">", "value": value}


def _gte(field: str, value: Any) -> Dict[str, Any]:
    return {"field": field, "operator": ">=", "value": value}


def _lt(field: str, value: Any) -> Dict[str, Any]:
    return {"field": field, "operator": "<", "value": value}


def _lte(field: str, value: Any) -> Dict[str, Any]:
    return {"field": field, "operator": "<=", "value": value}


def _exists(
    field: Optional[List[str]] = None,
):
    if not field:
        raise ValueError("field must be a non-empty list")
    # field 字段必须填，用于判断这是一个完整的查询条件，但后端不使用
    return {"field": "field", "operator": "exists", "value": field}


def _not_exists(
    field: Optional[List[str]] = None,
):
    if not field:
        raise ValueError("field must be a non-empty list")
    # field 字段必须填，用于判断这是一个完整的查询条件，但后端不使用
    return {"field": "field", "operator": "not exists", "value": field}


# def _like(field: str, pattern: str) -> Dict[str, Any]:
#     return {"field": field, "operator": "like", "value": pattern}


def _in(field: str, values: list) -> dict:
    """
    构建 IN 条件，对应 operator = "in"

    Args:
        field (str): 字段名
        values (list): 值列表，非空

    Returns:
        dict: {"field": ..., "operator": "in", "value": [...]}

    Raises:
        ValueError: 如果 values 不是 list 或为空
    """
    if not isinstance(values, list):
        raise ValueError("values 必须是一个列表")
    if not values:
        raise ValueError("IN 条件的值列表不能为空")
    return {"field": field, "operator": "in", "value": values}


# ===== 排序构建 =====


def sort_field(field: str, order: str = "asc") -> Dict[str, str]:
    """
    构建单个排序项

    :param field: 字段名，如 "created_time"
    :param order: 排序方向，"asc" 或 "desc"
    :return: {"field": "...", "sort": "..."}
    """
    if order not in ("asc", "desc"):
        raise ValueError("order must be 'asc' or 'desc'")
    if not field or not isinstance(field, str):
        raise ValueError("field must be a non-empty string")
    return {"field": field, "sort": order}


def condition_to_str(condition: Dict[str, Any]) -> str:
    """
    将 ES 查询条件 dict 倒推为 es_query_builder 函数调用形式的字符串。

    示例:
        >>> condition_to_str({"field": "status", "operator": "==", "value": "active"})
        '_eq("status", "active")'
        >>> condition_to_str({"logic": "and", "conditions": [{"field": "status", "operator": "==", "value": "active"}]})
        'and_(_eq("status", "active"))'

    Args:
        condition: 由 es_query_builder 构建的标准查询条件 dict

    Returns:
        对应的函数调用形式字符串，如 and_(_eq(...), _gt(...))
    """
    if not isinstance(condition, dict):
        raise ApiBaseError(
            f"condition必须是字典类型 (condition类型={type(condition).__name__!r})"
        )

    # 逻辑组合节点
    if "logic" in condition:
        logic = condition["logic"]
        conditions = condition.get("conditions", [])
        if not conditions:
            return f"{logic}_()"
        inner = ", ".join(condition_to_str(c) for c in conditions)
        return f"{logic}_({inner})"

    # 叶子节点
    if "field" in condition:
        field = condition["field"]
        operator = condition.get("operator", "==")
        value = condition.get("value")

        op_map = {
            "==": "_eq",
            "!=": "_neq",
            ">": "_gt",
            ">=": "_gte",
            "<": "_lt",
            "<=": "_lte",
            "in": "_in",
            "exists": "_exists",
            "not_exists": "_not_exists",
        }
        func = op_map.get(operator)
        if func is None:
            # 未知操作符，回退到 field_condition
            return f"field_condition({field!r}, {operator!r}, {value!r})"

        if func in ("_exists", "_not_exists"):
            return f"{func}({value!r})"
        return f"{func}({field!r}, {value!r})"

    raise ApiBaseError(f"condition格式不合法 (condition={condition!r})")
