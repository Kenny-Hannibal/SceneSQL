#!/usr/bin/env python3
import inspect
from functools import wraps
from typing import Any, get_args, get_origin


def _get_inner_function(f):
    while hasattr(f, "__wrapped__"):
        f = f.__wrapped__
    return f


def check_type(value, type_):
    if type_ is Any:
        return value
    if type_ is None:
        if value is None:
            return value
        raise TypeError

    if type_ is bool:
        if isinstance(value, bool):
            return value
        raise TypeError

    if type_ is int:
        if not isinstance(value, bool) and isinstance(value, int):
            return value
        raise TypeError

    if type_ is str:
        if isinstance(value, str):
            return value
        raise TypeError

    if type_ is list or get_origin(type_) is list:
        if value is None:
            # None is OK to be the default value of a list
            return []
        if not isinstance(value, list):
            raise TypeError
        if get_args(type_):
            elem_type = get_args(type_)[0]
            for elem in value:
                check_type(elem, elem_type)
        return value

    if type_ is tuple or get_origin(type_) is tuple:
        if value is None:
            return ()
        if not isinstance(value, tuple):
            raise TypeError
        args = get_args(type_)
        if not args:
            return value
        if len(args) == 2 and args[1] is Ellipsis:
            for elem in value:
                check_type(elem, args[0])
            return value
        if len(value) != len(args):
            raise TypeError
        for elem, elem_type in zip(value, args):
            check_type(elem, elem_type)
        return value

    if type_ is dict or get_origin(type_) is dict:
        if value is None:
            # None is OK to be the default value of a dict
            return {}
        if not isinstance(value, dict):
            raise TypeError
        return value

    return value


def checked(f):
    _inner_f = _get_inner_function(f)
    check_type_args = inspect.getfullargspec(_inner_f)
    check_type_annotations = check_type_args.annotations
    if not check_type_annotations:
        return f

    @wraps(f)
    def _f(*args, **kwargs):
        call_args = inspect.getcallargs(_inner_f, *args, **kwargs)
        for k, v in list(call_args.items()):
            if k in check_type_annotations:
                try:
                    call_args[k] = check_type(v, check_type_annotations[k])
                except TypeError:
                    raise TypeError(
                        f"{k}={v} can not match type {check_type_annotations[k]}"
                    )

        # Create arguments
        args = [call_args.pop(a) for a in check_type_args.args]
        if check_type_args.varargs is not None:
            args.extend(call_args.pop(check_type_args.varargs))
        if check_type_args.varkw is not None:
            kwargs = call_args.pop(check_type_args.varkw)
        else:
            kwargs = {}
        kwargs.update(call_args)
        _return = f(*args, **kwargs)
        if "return" not in check_type_annotations:
            return _return
        try:
            return check_type(_return, check_type_annotations["return"])
        except TypeError:
            raise TypeError(
                f"<return>={_return} can not match type {check_type_annotations['return']}"
            )

    return _f
