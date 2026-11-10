from __future__ import annotations

from pathlib import Path
from typing import Optional


def inspect_ifc(path: Path) -> Optional[dict]:
    try:
        import ifcopenshell
    except ImportError:
        return None
    model = ifcopenshell.open(str(path))
    projects = model.by_type("IfcProject")
    buildings = model.by_type("IfcBuilding")
    storeys = model.by_type("IfcBuildingStorey")
    return {
        "schema": getattr(model, "schema", ""),
        "project": getattr(projects[0], "Name", None) if projects else None,
        "buildings": len(buildings),
        "storeys": len(storeys),
    }
