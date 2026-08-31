from .datamining_db import DataminingDbMixin
from .file import FileMixin
from .frame import FrameMixin
from .label import LabelMixin
from .metadata import MetadataMixin
from .search import SearchMixin
from .tag import TagMixin


class ProdDataClient(
    DataminingDbMixin,
    MetadataMixin,
    TagMixin,
    FileMixin,
    LabelMixin,
    SearchMixin,
    FrameMixin,
):
    """ProdData 客户端，通过 Mixin 组合各领域功能。"""
