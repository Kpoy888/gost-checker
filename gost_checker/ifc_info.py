from __future__ import annotations

from pathlib import Path
from typing import Optional


def inspect_ifc(path: Path) -> Optional[dict]:
    try:
        import ifcopenshell
    except ImportError:
        return None
    try:
        model = ifcopenshell.open(str(path))
    except Exception as exc:
        return {"error": f"IFC не прочитан: {exc}"}
    try:
        projects = model.by_type("IfcProject")
        buildings = model.by_type("IfcBuilding")
        storeys = model.by_type("IfcBuildingStorey")
    except Exception as exc:
        return {"error": f"IFC открыт, но сведения не разобраны: {exc}"}
    return {
        "schema": getattr(model, "schema", ""),
        "project": getattr(projects[0], "Name", None) if projects else None,
        "buildings": len(buildings),
        "storeys": len(storeys),
    }
