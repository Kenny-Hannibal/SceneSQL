from enum import Enum


class MemberSource(str, Enum):
    """成员来源类型

    DEFAULT: 使用成员自身 id（默认）
    ORIGIN:  使用成员的 origins
    PARENT:  使用成员的 parents
    """

    DEFAULT = "default"
    ORIGIN = "origin"
    PARENT = "parent"


class ClipMatchMode(str, Enum):
    """Clip 同源匹配模式

    BIN_STRICT: bin 严格模式 - 同源 bin id + 同源 bin table + start_timestamp + end_timestamp
    BIN_LOOSE:  bin 宽松模式 - 同源 bin id + 同源 bin table
    RAW_LOOSE:  bag 同源模式 - 原始 bag id + 同源 bag table
    """

    BIN_STRICT = "bin_strict"
    BIN_LOOSE = "bin_loose"
    RAW_LOOSE = "raw_loose"
