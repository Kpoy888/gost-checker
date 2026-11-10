from .base import BaseCheck, CheckContext
from .dimensions import DimensionsCheck
from .fonts import FontsCheck
from .layout import LayoutOverlapCheck
from .lines import LinesCheck
from .linetypes import LineTypesCheck
from .scales import ScalesCheck
from .sheet_frame import SheetFrameCheck
from .title_block import TitleBlockCheck

ALL_CHECKS: list[BaseCheck] = [
    SheetFrameCheck(),
    TitleBlockCheck(),
    LineTypesCheck(),
    LinesCheck(),
    FontsCheck(),
    ScalesCheck(),
    DimensionsCheck(),
    LayoutOverlapCheck(),
]

__all__ = ["ALL_CHECKS", "BaseCheck", "CheckContext"]
