#!/usr/bin/env python3


def obj_to_dict(obj, omit_empty=True, _visited_cache=None):
    """Convert any object to dict. You can customize how one class should be converted to dict by
    implement object_to_dict method under the class
        def object_to_dict() -> dict

    Args:
        obj ([type]): [description]
        omit_empty (bool, optional): ignore empty fields. Defaults to True.
        _visited_cache (dict, optional): _visited_cache is a dictionary used to keep track of the objects that have been visited and their corresponding converted values.

    Returns:
        json dict
    """
    if isinstance(
        obj, (int, str, bool, float, type(None))
    ):  # 不可变对象直接返回
        return obj
    if _visited_cache is None:
        _visited_cache = {}
    obj_id = id(obj)
    if obj_id in _visited_cache:
        return _visited_cache[obj_id]

    if isinstance(obj, dict):
        res = {}
        _visited_cache[obj_id] = res
        for k, v in obj.items():
            if not is_empty(v) or not omit_empty:
                res[k] = obj_to_dict(v, omit_empty, _visited_cache)
        return res
    elif getattr(obj, "object_to_dict", None):
        return obj.object_to_dict()
    elif hasattr(obj, "__dict__") and isinstance(obj.__dict__, dict):
        res = {}
        _visited_cache[obj_id] = res
        for k, v in obj.__dict__.items():
            if not is_empty(v) or not omit_empty:
                res[k] = obj_to_dict(v, omit_empty, _visited_cache)
        return res
    elif isinstance(obj, (list, tuple, set)):
        res = []
        _visited_cache[obj_id] = res
        for x in obj:
            if not is_empty(x) or not omit_empty:
                res.append(obj_to_dict(x, omit_empty, _visited_cache))
        return res
    else:
        return obj


def is_empty(obj):
    if obj == {} or obj is None:
        return True
    return False
