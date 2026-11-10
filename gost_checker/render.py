from __future__ import annotations

from pathlib import Path
from typing import Any, Optional


def render_dxf_to_png(doc: Any, dest: Path, size=None, dpi: Optional[int] = None) -> Optional[str]:
    """Растрировать чертёж через надстройку ezdxf + matplotlib. При ошибке вернуть None."""
    from .runtime_limits import preview_figure_size_dpi

    default_size, default_dpi = preview_figure_size_dpi()
    if size is None:
        size = default_size
    if dpi is None:
        dpi = default_dpi
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        from ezdxf.addons.drawing import Frontend, RenderContext
        from ezdxf.addons.drawing.config import BackgroundPolicy, Configuration
        from ezdxf.addons.drawing.matplotlib import MatplotlibBackend

        fig = plt.figure(figsize=size)
        ax = fig.add_axes([0, 0, 1, 1])
        ctx = RenderContext(doc)
        backend = MatplotlibBackend(ax)
        try:
            cfg = Configuration(background_policy=BackgroundPolicy.WHITE)
            Frontend(ctx, backend, config=cfg).draw_layout(doc.modelspace(), finalize=True)
        except TypeError:
            Frontend(ctx, backend).draw_layout(doc.modelspace(), finalize=True)
        fig.savefig(dest, dpi=dpi)
        plt.close(fig)
        return str(dest)
    except Exception:
        # запасной контурный рисунок по примитивам
        try:
            return _fallback_plot(doc, dest, size, dpi)
        except Exception:
            return None


def _fallback_plot(doc: Any, dest: Path, size, dpi: int) -> Optional[str]:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(figsize=size)
    ax.set_aspect("equal")
    ax.set_facecolor("white")
    msp = doc.modelspace()
    for entity in msp:
        dtype = entity.dxftype()
        if dtype == "LINE":
            ax.plot(
                [entity.dxf.start.x, entity.dxf.end.x],
                [entity.dxf.start.y, entity.dxf.end.y],
                color="black",
                linewidth=0.6,
            )
        elif dtype == "LWPOLYLINE":
            pts = list(entity.get_points("xy"))
            if not pts:
                continue
            if entity.closed:
                pts = list(pts) + [pts[0]]
            ax.plot([p[0] for p in pts], [p[1] for p in pts], color="black", linewidth=0.6)
        elif dtype == "CIRCLE":
            circ = plt.Circle(
                (entity.dxf.center.x, entity.dxf.center.y),
                entity.dxf.radius,
                fill=False,
                color="black",
                linewidth=0.6,
            )
            ax.add_patch(circ)
        elif dtype == "TEXT":
            ax.text(entity.dxf.insert.x, entity.dxf.insert.y, entity.dxf.text or "", fontsize=6)
    ax.autoscale()
    fig.savefig(dest, dpi=dpi, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    return str(dest)
