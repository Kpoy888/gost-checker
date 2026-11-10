from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Optional


@dataclass
class Finding:
    message: str
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class CheckResult:
    check_id: str
    title: str
    gost: str
    severity: str
    message: str
    details: list[str] = field(default_factory=list)
    can_autofix: bool = False
    auto_fixed: bool = False
    findings_count: int = 0
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Rect:
    xmin: float
    ymin: float
    xmax: float
    ymax: float
    space: str = "Model"

    @property
    def width(self) -> float:
        return abs(self.xmax - self.xmin)

    @property
    def height(self) -> float:
        return abs(self.ymax - self.ymin)

    def as_tuple(self) -> tuple[float, float, float, float]:
        return (self.xmin, self.ymin, self.xmax, self.ymax)


@dataclass
class FrameInfo:
    format_name: Optional[str] = None
    orientation: Optional[str] = None  # landscape | portrait
    kind: Optional[str] = None  # outer | inner
    outer: Optional[Rect] = None
    inner: Optional[Rect] = None
    space: str = "Model"
    margins: Optional[dict] = None
    rotation_deg: float = 0.0


@dataclass
class ProcessResult:
    input_path: str
    kind: str
    output_dxf: Optional[str] = None
    output_dwg: Optional[str] = None
    report_html: Optional[str] = None
    report_md: Optional[str] = None
    report_json: Optional[str] = None
    render_before: Optional[str] = None
    render_after: Optional[str] = None
    render_overlay: Optional[str] = None
    checks: list[CheckResult] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    limitations: list[str] = field(default_factory=list)
    summary: dict[str, int] = field(default_factory=dict)
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["checks"] = [c.to_dict() if isinstance(c, CheckResult) else c for c in self.checks]
        return data
