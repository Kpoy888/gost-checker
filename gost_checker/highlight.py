"""Подсветка замечаний: пронумерованные рамки поверх листа."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional


def _color(severity: str) -> tuple[int, int, int]:
    if severity == "error":
        return (180, 40, 32)
    if severity == "warning":
        return (180, 110, 20)
    if severity == "markup":
        return (110, 40, 170)
    return (30, 90, 160)


def render_dxf_highlight(doc: Any, issues: list[dict], dest: Path, to_mm: float = 1.0) -> Optional[str]:
    """Рендер модели. Небольшие чертежи — ezdxf+matplotlib, остальные — быстрый контур."""
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        count = sum(1 for _ in doc.modelspace())
    except Exception:
        count = 9999
    if count <= 280:
        drawn = _render_matplotlib(doc, issues, dest, to_mm)
        if drawn:
            return drawn
    return _render_fast(doc, issues, dest, to_mm)


def _issue_box_model(issue: dict, to_mm: float):
    bbox = issue.get("bbox")
    if not bbox or len(bbox) < 4:
        return None
    space = issue.get("space") or "model"
    xmin, ymin, xmax, ymax = (float(v) for v in bbox[:4])
    if space == "mm":
        scale = to_mm if to_mm else 1.0
        xmin, ymin, xmax, ymax = xmin / scale, ymin / scale, xmax / scale, ymax / scale
    elif space == "pixel":
        return None
    return xmin, ymin, xmax, ymax


def _render_matplotlib(doc, issues, dest: Path, to_mm: float) -> Optional[str]:
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        from matplotlib.patches import Rectangle
        from ezdxf.addons.drawing import Frontend, RenderContext
        from ezdxf.addons.drawing.config import BackgroundPolicy, Configuration
        from ezdxf.addons.drawing.matplotlib import MatplotlibBackend

        fig = plt.figure(figsize=(8.2, 5.8))
        ax = fig.add_axes([0.02, 0.02, 0.96, 0.96])
        ctx = RenderContext(doc)
        backend = MatplotlibBackend(ax)
        try:
            cfg = Configuration(background_policy=BackgroundPolicy.WHITE)
            Frontend(ctx, backend, config=cfg).draw_layout(doc.modelspace(), finalize=True)
        except TypeError:
            Frontend(ctx, backend).draw_layout(doc.modelspace(), finalize=True)
        _draw_axes_markers(ax, issues, to_mm)
        fig.savefig(dest, dpi=80)
        plt.close(fig)
        return str(dest)
    except Exception:
        return None


def _draw_axes_markers(ax, issues, to_mm: float) -> None:
    from matplotlib.patches import Rectangle

    for issue in issues:
        box = _issue_box_model(issue, to_mm)
        if not box:
            continue
        xmin, ymin, xmax, ymax = box
        color = tuple(c / 255 for c in _color(issue.get("severity") or ""))
        ax.add_patch(
            Rectangle(
                (xmin, ymin),
                max(xmax - xmin, 1e-6),
                max(ymax - ymin, 1e-6),
                fill=False,
                edgecolor=color,
                linewidth=1.4,
            )
        )
        ax.text(
            xmin,
            ymax,
            str(issue.get("n") or ""),
            color=color,
            fontsize=8,
            fontweight="bold",
            va="bottom",
        )


def _render_fast(doc, issues, dest: Path, to_mm: float) -> Optional[str]:
    try:
        from PIL import Image, ImageDraw, ImageFont
    except Exception:
        return None
    segs = []
    try:
        msp = doc.modelspace()
    except Exception:
        return None
    for entity in msp:
        dtype = entity.dxftype()
        try:
            if dtype == "LINE":
                segs.append((entity.dxf.start.x, entity.dxf.start.y, entity.dxf.end.x, entity.dxf.end.y))
            elif dtype == "LWPOLYLINE":
                pts = list(entity.get_points("xy"))
                if entity.closed and pts:
                    pts = list(pts) + [pts[0]]
                for a, b in zip(pts, pts[1:]):
                    segs.append((a[0], a[1], b[0], b[1]))
            elif dtype == "CIRCLE":
                c, r = entity.dxf.center, float(entity.dxf.radius)
                segs.append((c.x - r, c.y, c.x + r, c.y))
                segs.append((c.x, c.y - r, c.x, c.y + r))
            elif dtype in {"TEXT", "MTEXT"}:
                ins = entity.dxf.insert
                segs.append((ins.x, ins.y, ins.x + 2, ins.y + 2))
        except Exception:
            continue
        if len(segs) > 4000:
            break
    boxes = []
    for issue in issues:
        box = _issue_box_model(issue, to_mm)
        if box:
            boxes.append((box, issue))
    xs = [v for seg in segs for v in (seg[0], seg[2])]
    ys = [v for seg in segs for v in (seg[1], seg[3])]
    for (xmin, ymin, xmax, ymax), _issue in boxes:
        xs.extend((xmin, xmax))
        ys.extend((ymin, ymax))
    if not xs:
        xs, ys = [0, 420], [0, 297]
    minx, maxx = min(xs), max(xs)
    miny, maxy = min(ys), max(ys)
    spanx = max(maxx - minx, 1.0)
    spany = max(maxy - miny, 1.0)
    width, height = 900, 640
    pad = 16
    sx = (width - 2 * pad) / spanx
    sy = (height - 2 * pad) / spany
    scale = min(sx, sy)

    def project(x, y):
        px = pad + (x - minx) * scale
        py = height - pad - (y - miny) * scale
        return px, py

    image = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(image)
    for x1, y1, x2, y2 in segs:
        draw.line([project(x1, y1), project(x2, y2)], fill=(20, 20, 20), width=1)
    try:
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 14)
    except Exception:
        font = ImageFont.load_default()
    for (xmin, ymin, xmax, ymax), issue in boxes:
        p1 = project(xmin, ymax)
        p2 = project(xmax, ymin)
        color = _color(issue.get("severity") or "")
        draw.rectangle([p1, p2], outline=color, width=2)
        draw.text(p1, str(issue.get("n") or ""), fill=color, font=font)
    image.save(dest)
    return str(dest)


def render_raster_highlight(image_bgr, issues: list[dict], dest: Path) -> Optional[str]:
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        import numpy as np
        from PIL import Image, ImageDraw, ImageFont
    except Exception:
        return None
    if image_bgr is None:
        return None
    arr = np.asarray(image_bgr)
    if arr.ndim == 2:
        rgb = np.stack((arr, arr, arr), axis=-1)
    else:
        rgb = np.ascontiguousarray(arr[:, :, :3][:, :, ::-1])
    image = Image.fromarray(rgb).convert("RGB")
    draw = ImageDraw.Draw(image)
    try:
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 16)
    except Exception:
        font = ImageFont.load_default()
    width, height = image.size
    for issue in issues:
        bbox = issue.get("bbox")
        if not bbox or (issue.get("space") not in {None, "pixel"}):
            continue
        xmin, ymin, xmax, ymax = [float(v) for v in bbox[:4]]
        xmin, xmax = sorted((max(0, xmin), min(width - 1, xmax)))
        ymin, ymax = sorted((max(0, ymin), min(height - 1, ymax)))
        color = _color(issue.get("severity") or "")
        draw.rectangle([xmin, ymin, xmax, ymax], outline=color, width=3)
        draw.text((xmin + 2, ymin + 2), str(issue.get("n") or ""), fill=color, font=font)
    image.save(dest)
    return str(dest)
