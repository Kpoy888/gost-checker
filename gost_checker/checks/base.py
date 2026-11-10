from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from ..models import CheckResult, FrameInfo


@dataclass
class CheckContext:
    doc: Any
    path: Path
    to_mm: float
    fix: bool = False
    title_meta: dict = field(default_factory=dict)
    assumed_format: str = "A3"
    assign_layers: bool = False
    extras: dict = field(default_factory=dict)

    @property
    def frame(self) -> Optional[FrameInfo]:
        return self.extras.get("frame")

    @frame.setter
    def frame(self, value: FrameInfo) -> None:
        self.extras["frame"] = value


class BaseCheck(ABC):
    id: str = ""
    title: str = ""
    gost: str = ""
    can_autofix: bool = False

    @abstractmethod
    def run(self, ctx: CheckContext) -> CheckResult:
        raise NotImplementedError

    def result(
        self,
        severity: str,
        message: str,
        details: Optional[list[str]] = None,
        auto_fixed: bool = False,
        findings_count: int = 0,
        extra: Optional[dict] = None,
    ) -> CheckResult:
        return CheckResult(
            check_id=self.id,
            title=self.title,
            gost=self.gost,
            severity=severity,
            message=message,
            details=details or [],
            can_autofix=self.can_autofix,
            auto_fixed=auto_fixed,
            findings_count=findings_count,
            extra=extra or {},
        )
