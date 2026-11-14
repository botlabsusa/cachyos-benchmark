"""Dark steampunk theme for the CachyOS Benchmark GUI (v1.0.3).

Everything is drawn in code: no image or font files are bundled. Small SVGs
(check mark, arrows) are written to a private temp dir at startup and removed
on exit. Qt imports are lazy so the CLI paths never load Qt.
"""
from __future__ import annotations

import atexit
import math
import shutil
import tempfile
from pathlib import Path

# ---------------------------------------------------------------------------
# Palette
# ---------------------------------------------------------------------------
C = {
    "bg0": "#14110f",      # window / deepest
    "bg1": "#1c1814",      # plates
    "bg2": "#26201a",      # raised / inputs
    "bg3": "#312920",      # hover
    "line": "#3a2f23",     # grid / dividers
    "brass": "#b5893b",
    "brass_hi": "#d4a94f",
    "brass_lo": "#7a5a24",
    "copper": "#b8693d",
    "copper_hi": "#d98a5a",
    "copper_lo": "#7a3f22",
    "teal": "#4f8f84",
    "teal_hi": "#6fb3a6",
    "text": "#e8dcc4",
    "text_dim": "#a89a80",
    "text_mute": "#7d725f",
    "ink": "#1a130c",      # text on brass
    "good": "#79b07a",     # faster / better (desaturated green)
    "bad": "#d0644f",      # slower / worse (desaturated red)
    "noise": "#8a8070",
    "amber": "#e0a23a",
    "red": "#d0533f",
}

DISPLAY_FAMILIES = [
    "Noto Serif Display", "Noto Serif", "Source Serif 4", "Source Serif Pro",
    "DejaVu Serif", "Liberation Serif", "Georgia", "serif",
]
MONO_FAMILIES = ["JetBrains Mono", "Hack", "Noto Sans Mono", "DejaVu Sans Mono", "Liberation Mono", "monospace"]

_ASSET_DIR: Path | None = None

_SVGS = {
    "check.svg": (
        '<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 16 16">'
        '<path d="M3.2 8.4 L6.6 11.6 L12.9 4.6" fill="none" stroke="#1a130c" stroke-width="2.4" '
        'stroke-linecap="round" stroke-linejoin="round"/></svg>'
    ),
    "down.svg": (
        '<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 12 12">'
        '<path d="M2 4 L6 8.5 L10 4" fill="none" stroke="#d4a94f" stroke-width="1.8" '
        'stroke-linecap="round" stroke-linejoin="round"/></svg>'
    ),
    "sort_up.svg": (
        '<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10" viewBox="0 0 10 10">'
        '<path d="M1.5 7 L5 3 L8.5 7 Z" fill="#1a130c"/></svg>'
    ),
    "sort_down.svg": (
        '<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10" viewBox="0 0 10 10">'
        '<path d="M1.5 3 L5 7 L8.5 3 Z" fill="#1a130c"/></svg>'
    ),
}


def asset_dir() -> Path:
    global _ASSET_DIR
    if _ASSET_DIR is None:
        d = Path(tempfile.mkdtemp(prefix="cachybench-theme-"))
        for name, svg in _SVGS.items():
            (d / name).write_text(svg)
        atexit.register(shutil.rmtree, d, True)
        _ASSET_DIR = d
    return _ASSET_DIR


def _brass_grad(top="#e2bd6c", mid="#b5893b", bot="#7a5a24") -> str:
    return (f"qlineargradient(x1:0,y1:0,x2:0,y2:1, stop:0 {top}, stop:0.48 {mid}, "
            f"stop:0.52 {mid}, stop:1 {bot})")


def stylesheet() -> str:
    a = asset_dir().as_posix()
    c = C
    btn = _brass_grad("#5a4626", "#46361e", "#2e2414")
    btn_hover = _brass_grad("#6e5630", "#584426", "#3a2d19")
    brass = _brass_grad()
    brass_hover = _brass_grad("#f0cd80", "#c99a46", "#8a682b")
    copper = _brass_grad("#e39a6a", "#b8693d", "#7a3f22")
    copper_hover = _brass_grad("#f0ad7e", "#c97848", "#8a4a28")
    return f"""
* {{ outline: 0; }}
QMainWindow, QWidget#centralRoot {{ background: {c['bg0']}; }}
QWidget {{ color: {c['text']}; }}
QToolTip {{ background: {c['bg2']}; color: {c['text']}; border: 1px solid {c['brass']}; padding: 4px 6px; }}
QLabel {{ background: transparent; }}
QLabel[role="hint"] {{ color: {c['text_dim']}; }}
QLabel[role="caption"] {{ color: {c['brass_hi']}; font-weight: 600; letter-spacing: 0.5px; }}
QLabel[role="meta"] {{ color: {c['text']}; font-family: "{MONO_FAMILIES[2]}", "DejaVu Sans Mono", monospace; }}

/* Menu bar */
QMenuBar {{ background: {c['bg0']}; color: {c['text_dim']}; border-bottom: 1px solid #2a221a; padding: 2px 6px; }}
QMenuBar::item {{ padding: 4px 10px; background: transparent; border-radius: 4px; }}
QMenuBar::item:selected {{ background: {c['bg2']}; color: {c['brass_hi']}; }}
QMenu {{ background: {c['bg1']}; border: 1px solid {c['brass_lo']}; padding: 4px; }}
QMenu::item {{ padding: 6px 22px 6px 14px; border-radius: 4px; }}
QMenu::item:selected {{ background: rgba(212,169,79,0.22); color: {c['brass_hi']}; }}
QMenu::item:disabled {{ color: {c['text_mute']}; }}
QMenu::separator {{ height: 1px; background: {c['line']}; margin: 4px 8px; }}

/* Tabs: brass tabs on a dark rail */
QTabWidget::pane {{ border: none; border-top: 2px solid {c['brass_lo']}; top: -2px; background: {c['bg0']}; }}
QTabWidget::tab-bar {{ left: 14px; }}
QTabBar {{ qproperty-drawBase: 0; }}
QTabBar::tab {{
  background: {_brass_grad('#2c241b', '#221c15', '#1a1510')};
  color: {c['text_dim']}; border: 1px solid #4a3a22; border-bottom: none;
  border-top-left-radius: 9px; border-top-right-radius: 9px;
  padding: 8px 26px; margin-right: 4px; margin-top: 4px; min-width: 90px;
  font-weight: 600; letter-spacing: 0.6px;
}}
QTabBar::tab:hover {{ color: {c['brass_hi']}; border-color: {c['brass']}; }}
QTabBar::tab:selected {{
  background: {brass}; color: {c['ink']}; border: 1px solid {c['brass_hi']}; border-bottom: none;
  margin-top: 0px; padding-top: 11px;
}}

/* Buttons */
QPushButton {{
  background: {btn}; color: {c['text']}; border: 1px solid {c['brass_lo']};
  border-top-color: #9c7838; border-radius: 7px; padding: 7px 16px; font-weight: 600;
}}
QPushButton:hover {{ background: {btn_hover}; border-color: {c['brass']}; color: #fff4de; }}
QPushButton:pressed {{ background: #241c12; border-color: {c['brass_hi']}; padding-top: 8px; padding-bottom: 6px; }}
QPushButton:disabled {{ background: #1e1913; color: {c['text_mute']}; border-color: #3a2f22; }}
QPushButton:focus {{ border-color: {c['brass_hi']}; }}
QPushButton[kind="brass"] {{ background: {brass}; color: {c['ink']}; border: 1px solid #e6c47a; border-bottom-color: #5e4519; }}
QPushButton[kind="brass"]:hover {{ background: {brass_hover}; color: #000; }}
QPushButton[kind="brass"]:pressed {{ background: {_brass_grad('#8a682b', '#a77d36', '#c99a46')}; }}
QPushButton[kind="danger"] {{ background: {_brass_grad('#6a3424', '#55291b', '#3a1b11')}; border-color: #8a4a30; color: #f3d9c8; }}
QPushButton[kind="danger"]:hover {{ background: {_brass_grad('#80402c', '#663122', '#452015')}; border-color: {c['copper_hi']}; }}
QPushButton[kind="danger"]:disabled {{ background: #1e1913; color: {c['text_mute']}; border-color: #3a2f22; }}
QPushButton#runButton {{
  background: {copper}; color: #fff5e6; border: 1px solid #f0b48a; border-bottom-color: #4e2614;
  border-radius: 10px; padding: 10px 28px; letter-spacing: 1.2px;
}}
QPushButton#runButton:hover {{ background: {copper_hover}; }}
QPushButton#runButton:pressed {{ background: {_brass_grad('#7a3f22', '#9a5530', '#c97848')}; padding-top: 12px; padding-bottom: 8px; }}
QPushButton#runButton:disabled {{ background: {_brass_grad('#4a3024', '#3c271d', '#2c1c14')}; color: #a08470; border-color: #5a3a28; }}

/* Inputs */
QLineEdit, QComboBox, QPlainTextEdit, QTextEdit {{
  background: {c['bg0']}; color: {c['text']}; border: 1px solid #4a3a24; border-radius: 6px;
  selection-background-color: rgba(212,169,79,0.45); selection-color: #fff6e2;
}}
QLineEdit, QComboBox {{ padding: 6px 10px; min-height: 22px; }}
QLineEdit:focus, QComboBox:focus, QPlainTextEdit:focus {{ border-color: {c['brass']}; }}
QLineEdit:hover, QComboBox:hover {{ border-color: {c['brass_lo']}; }}
QComboBox::drop-down {{ width: 26px; border: none; border-left: 1px solid #3a2f22; }}
QComboBox::down-arrow {{ image: url({a}/down.svg); width: 12px; height: 12px; }}
QComboBox QAbstractItemView {{
  background: {c['bg1']}; border: 1px solid {c['brass_lo']}; color: {c['text']};
  selection-background-color: rgba(212,169,79,0.30); selection-color: #fff6e2; padding: 2px;
}}
QPlainTextEdit {{ padding: 6px; }}
QPlainTextEdit#console {{ background: #0f0d0b; color: #d9c9a6; border: 1px solid #3a2f22; }}

/* Checkboxes (widgets and table items) */
QCheckBox {{ spacing: 8px; color: {c['text']}; }}
QCheckBox::indicator, QTableView::indicator {{
  width: 16px; height: 16px; border: 1px solid {c['brass_lo']}; border-radius: 4px; background: {c['bg0']};
}}
QCheckBox::indicator:hover, QTableView::indicator:hover {{ border-color: {c['brass_hi']}; }}
QCheckBox::indicator:checked, QTableView::indicator:checked {{
  background: {brass}; border-color: {c['brass_hi']}; image: url({a}/check.svg);
}}

/* Tables */
QTableView {{
  background: #18140f; alternate-background-color: #1f1a14; color: {c['text']};
  gridline-color: #2c241b; border: 1px solid #3a2f22; border-radius: 6px;
  selection-background-color: #4f3c1c; selection-color: #fff6e2;
}}
QTableView::item {{ padding: 4px 8px; border: none; }}
QTableView::item:selected {{ background: #4f3c1c; color: #fff6e2; border-top: 1px solid #8a682b; border-bottom: 1px solid #8a682b; }}
QTableView::item:hover {{ background: rgba(212,169,79,0.08); }}
QHeaderView {{ background: transparent; }}
QHeaderView::section {{
  background: {brass}; color: {c['ink']}; font-weight: 700; padding: 6px 10px;
  border: none; border-right: 1px solid #8a682b; border-bottom: 1px solid #5e4519;
}}
QHeaderView::section:hover {{ background: {brass_hover}; }}
QHeaderView::up-arrow {{ image: url({a}/sort_up.svg); width: 10px; height: 10px; subcontrol-position: center right; padding-right: 4px; }}
QHeaderView::down-arrow {{ image: url({a}/sort_down.svg); width: 10px; height: 10px; subcontrol-position: center right; padding-right: 4px; }}
QTableCornerButton::section {{ background: {c['brass']}; border: none; }}

/* Scrollbars: slim brass rails */
QScrollBar:vertical {{ background: #15110d; width: 12px; margin: 2px; border-radius: 5px; }}
QScrollBar:horizontal {{ background: #15110d; height: 12px; margin: 2px; border-radius: 5px; }}
QScrollBar::handle:vertical {{ background: {_brass_grad('#8a682b', '#6e5222', '#55401b').replace('x2:0,y2:1', 'x2:1,y2:0')}; min-height: 30px; border-radius: 4px; }}
QScrollBar::handle:horizontal {{ background: {_brass_grad('#8a682b', '#6e5222', '#55401b')}; min-width: 30px; border-radius: 4px; }}
QScrollBar::handle:hover {{ background: {c['brass']}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: none; }}

QSplitter::handle {{ background: transparent; }}
QSplitter::handle:vertical {{ height: 10px; }}
QSplitter::handle:horizontal {{ width: 10px; }}

/* Dialogs */
QMessageBox {{ background: {c['bg1']}; }}
QMessageBox QLabel {{ color: {c['text']}; }}
QDialog {{ background: {c['bg1']}; }}
"""


def apply_app_theme(app) -> None:
    from PySide6.QtGui import QColor, QFont, QPalette
    from PySide6.QtWidgets import QStyleFactory

    if "Fusion" in QStyleFactory.keys():
        app.setStyle("Fusion")
    pal = QPalette()
    role = QPalette.ColorRole
    for r, col in (
        (role.Window, C["bg0"]), (role.WindowText, C["text"]), (role.Base, "#18140f"),
        (role.AlternateBase, "#1f1a14"), (role.Text, C["text"]), (role.Button, C["bg2"]),
        (role.ButtonText, C["text"]), (role.Highlight, "#4f3c1c"), (role.HighlightedText, "#fff6e2"),
        (role.ToolTipBase, C["bg2"]), (role.ToolTipText, C["text"]), (role.PlaceholderText, C["text_mute"]),
        (role.Link, C["brass_hi"]), (role.Mid, C["text_dim"]), (role.Light, "#4a3a24"),
        (role.Dark, "#0c0a08"), (role.Shadow, "#000000"), (role.BrightText, "#ffffff"),
    ):
        pal.setColor(r, QColor(col))
    pal.setColor(QPalette.ColorGroup.Disabled, role.Text, QColor(C["text_mute"]))
    pal.setColor(QPalette.ColorGroup.Disabled, role.ButtonText, QColor(C["text_mute"]))
    pal.setColor(QPalette.ColorGroup.Disabled, role.WindowText, QColor(C["text_mute"]))
    app.setPalette(pal)
    app.setStyleSheet(stylesheet())


def display_font(point_size: float, weight: int = 700):
    from PySide6.QtGui import QFont

    f = QFont()
    f.setFamilies(DISPLAY_FAMILIES)
    f.setPointSizeF(point_size)
    f.setWeight(QFont.Weight(weight))
    return f


def mono_font(point_size: float | None = None):
    from PySide6.QtGui import QFont

    f = QFont()
    f.setFamilies(MONO_FAMILIES)
    f.setStyleHint(QFont.StyleHint.Monospace)
    if point_size:
        f.setPointSizeF(point_size)
    return f


# ---------------------------------------------------------------------------
# matplotlib
# ---------------------------------------------------------------------------
def apply_mpl_theme() -> None:
    import matplotlib as mpl

    mpl.rcParams.update({
        "figure.facecolor": C["bg1"],
        "axes.facecolor": "#15110d",
        "axes.edgecolor": C["brass_lo"],
        "axes.labelcolor": C["text_dim"],
        "axes.titlecolor": C["brass_hi"],
        "axes.grid": True,
        "axes.axisbelow": True,
        "grid.color": "#3a2f23",
        "grid.linewidth": 0.6,
        "grid.alpha": 0.9,
        "xtick.color": C["text_dim"],
        "ytick.color": C["text_dim"],
        "xtick.labelcolor": C["text"],
        "ytick.labelcolor": C["text_dim"],
        "text.color": C["text"],
        "legend.facecolor": C["bg2"],
        "legend.edgecolor": C["brass_lo"],
        "legend.labelcolor": C["text"],
        "legend.framealpha": 0.9,
        "savefig.facecolor": C["bg1"],
    })


def style_axes(ax) -> None:
    """Brass spines on top/right hidden; brass bottom/left."""
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("bottom", "left"):
        ax.spines[side].set_color(C["brass_lo"])
        ax.spines[side].set_linewidth(1.0)
    ax.tick_params(length=3, color=C["brass_lo"])


# ---------------------------------------------------------------------------
# Painted widgets (classes built lazily so importing theme never needs Qt)
# ---------------------------------------------------------------------------
_WIDGETS: dict | None = None


def gear_path(cx: float, cy: float, r_out: float, r_in: float, teeth: int, hole: float, phase: float = 0.0):
    from PySide6.QtCore import QPointF
    from PySide6.QtGui import QPainterPath, QPolygonF

    pts = []
    step = 2 * math.pi / teeth
    for i in range(teeth):
        a0 = phase + i * step
        # trapezoid tooth: root, rise, top, fall
        for frac, r in ((0.0, r_in), (0.18, r_out), (0.50, r_out), (0.68, r_in)):
            a = a0 + frac * step
            pts.append(QPointF(cx + r * math.cos(a), cy + r * math.sin(a)))
    path = QPainterPath()
    path.addPolygon(QPolygonF(pts))
    path.closeSubpath()
    inner = QPainterPath()
    inner.addEllipse(QPointF(cx, cy), hole, hole)
    return path.subtracted(inner)


def widgets() -> dict:
    """Return {'Plate', 'HeaderBar', 'GaugeDial'} classes."""
    global _WIDGETS
    if _WIDGETS is not None:
        return _WIDGETS

    from PySide6.QtCore import QPointF, QRectF, Qt, QSize
    from PySide6.QtGui import (
        QBrush, QColor, QFont, QFontMetricsF, QLinearGradient, QPainter, QPainterPath, QPen,
        QRadialGradient, QConicalGradient,
    )
    from PySide6.QtWidgets import QFrame, QSizePolicy, QVBoxLayout, QWidget

    def rivet(p: QPainter, x: float, y: float, r: float = 3.6) -> None:
        g = QRadialGradient(QPointF(x - r * 0.35, y - r * 0.35), r * 1.4)
        g.setColorAt(0.0, QColor("#f6dc9c"))
        g.setColorAt(0.45, QColor("#b5893b"))
        g.setColorAt(1.0, QColor("#4a3412"))
        p.setPen(QPen(QColor(0, 0, 0, 150), 0.8))
        p.setBrush(QBrush(g))
        p.drawEllipse(QPointF(x, y), r, r)

    class Plate(QFrame):
        """Riveted, brass-framed panel with an engraved title."""

        def __init__(self, title: str = "", parent=None, rivets: bool = True) -> None:
            super().__init__(parent)
            self._title = title.upper()
            self._rivets = rivets
            self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, False)
            self.body = QVBoxLayout(self)
            top = 40 if title else 20
            self.body.setContentsMargins(20, top, 20, 18)
            self.body.setSpacing(10)
            self._tfont = display_font(10.5, 700)
            self._tfont.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 2.2)

        def setTitle(self, t: str) -> None:  # noqa: N802
            self._title = t.upper()
            self.update()

        def paintEvent(self, _ev) -> None:  # noqa: N802
            p = QPainter(self)
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            r = QRectF(self.rect()).adjusted(1.5, 1.5, -1.5, -1.5)
            # body
            g = QLinearGradient(r.topLeft(), r.bottomLeft())
            g.setColorAt(0.0, QColor("#231d17"))
            g.setColorAt(1.0, QColor("#19150f"))
            p.setPen(QPen(QColor("#6e5428"), 1.4))
            p.setBrush(QBrush(g))
            p.drawRoundedRect(r, 11, 11)
            # inner bevel
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QPen(QColor(212, 169, 79, 46), 1.0))
            p.drawRoundedRect(r.adjusted(4, 4, -4, -4), 8, 8)
            # rivets
            if self._rivets:
                for x, y in ((r.left() + 11, r.top() + 11), (r.right() - 11, r.top() + 11),
                             (r.left() + 11, r.bottom() - 11), (r.right() - 11, r.bottom() - 11)):
                    rivet(p, x, y)
            # engraved title + rule
            if self._title:
                p.setFont(self._tfont)
                fm = QFontMetricsF(self._tfont)
                tx, ty = r.left() + 26, r.top() + 12 + fm.ascent()
                p.setPen(QColor(0, 0, 0, 160))
                p.drawText(QPointF(tx + 1, ty + 1), self._title)
                p.setPen(QColor("#d4a94f"))
                p.drawText(QPointF(tx, ty), self._title)
                x0 = tx + fm.horizontalAdvance(self._title) + 12
                x1 = r.right() - 26
                if x1 > x0:
                    yy = ty - fm.ascent() * 0.35
                    lg = QLinearGradient(QPointF(x0, yy), QPointF(x1, yy))
                    lg.setColorAt(0.0, QColor(181, 137, 59, 170))
                    lg.setColorAt(1.0, QColor(181, 137, 59, 0))
                    p.setPen(QPen(QBrush(lg), 1.0))
                    p.drawLine(QPointF(x0, yy), QPointF(x1, yy))
            p.end()

    class HeaderBar(QWidget):
        """Title strip with two interlocking cogs drawn in code."""

        def __init__(self, title: str, subtitle: str, right_text: str, parent=None) -> None:
            super().__init__(parent)
            self._title, self._subtitle, self._right = title, subtitle, right_text
            self.setFixedHeight(84)
            self._phase = 0.0

        def set_phase(self, ph: float) -> None:
            self._phase = ph
            self.update()

        def paintEvent(self, _ev) -> None:  # noqa: N802
            p = QPainter(self)
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            w, h = self.width(), self.height()
            g = QLinearGradient(0, 0, 0, h)
            g.setColorAt(0.0, QColor("#2a221a"))
            g.setColorAt(0.6, QColor("#1d1813"))
            g.setColorAt(1.0, QColor("#15110d"))
            p.fillRect(self.rect(), g)
            # brass double rule at the bottom
            p.setPen(QPen(QColor("#b5893b"), 2))
            p.drawLine(0, h - 3, w, h - 3)
            p.setPen(QPen(QColor(212, 169, 79, 70), 1))
            p.drawLine(0, h - 7, w, h - 7)
            # faint engine-turned texture on the right
            p.setPen(QPen(QColor(212, 169, 79, 14), 1))
            for i in range(0, 420, 14):
                p.drawEllipse(QPointF(w - 120, h / 2), 40 + i * 0.35, 40 + i * 0.35)
            # cogs
            cy = h / 2 - 2
            big = gear_path(46, cy, 27, 21.5, 12, 7.5, self._phase)
            bg = QRadialGradient(QPointF(40, cy - 8), 34)
            bg.setColorAt(0.0, QColor("#f0cf86"))
            bg.setColorAt(0.55, QColor("#b5893b"))
            bg.setColorAt(1.0, QColor("#6a4c1c"))
            p.setPen(QPen(QColor(0, 0, 0, 170), 1.0))
            p.setBrush(QBrush(bg))
            p.drawPath(big)
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QPen(QColor(60, 40, 12, 200), 1.2))
            p.drawEllipse(QPointF(46, cy), 14, 14)
            small = gear_path(80, cy + 15, 15, 11.5, 9, 4.5, -self._phase * 12 / 9 + 0.2)
            sg = QRadialGradient(QPointF(76, cy + 10), 20)
            sg.setColorAt(0.0, QColor("#eaa478"))
            sg.setColorAt(0.6, QColor("#b8693d"))
            sg.setColorAt(1.0, QColor("#5e2f18"))
            p.setPen(QPen(QColor(0, 0, 0, 170), 1.0))
            p.setBrush(QBrush(sg))
            p.drawPath(small)
            # title
            tf = display_font(21, 700)
            tf.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 1.0)
            p.setFont(tf)
            fm = QFontMetricsF(tf)
            tx = 112
            ty = h / 2 - 2
            p.setPen(QColor(0, 0, 0, 180))
            p.drawText(QPointF(tx + 1.5, ty + 1.5), self._title)
            tg = QLinearGradient(0, ty - fm.ascent(), 0, ty)
            tg.setColorAt(0.0, QColor("#f3d79a"))
            tg.setColorAt(1.0, QColor("#c99a46"))
            p.setPen(QPen(QBrush(tg), 1))
            p.drawText(QPointF(tx, ty), self._title)
            sf = QFont()
            sf.setPointSizeF(10)
            sf.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 1.6)
            p.setFont(sf)
            p.setPen(QColor("#a89a80"))
            p.drawText(QPointF(tx + 2, ty + 22), self._subtitle.upper())
            # right plaque
            if self._right:
                rf = QFont()
                rf.setPointSizeF(10)
                rf.setWeight(QFont.Weight.DemiBold)
                rf.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 1.2)
                p.setFont(rf)
                rfm = QFontMetricsF(rf)
                tw = rfm.horizontalAdvance(self._right)
                pr = QRectF(w - tw - 58, h / 2 - 17, tw + 34, 30)
                pg = QLinearGradient(pr.topLeft(), pr.bottomLeft())
                pg.setColorAt(0.0, QColor("#e2bd6c"))
                pg.setColorAt(0.5, QColor("#b5893b"))
                pg.setColorAt(1.0, QColor("#7a5a24"))
                p.setPen(QPen(QColor("#4a3412"), 1))
                p.setBrush(QBrush(pg))
                p.drawRoundedRect(pr, 6, 6)
                rivet(p, pr.left() + 8, pr.center().y(), 2.4)
                rivet(p, pr.right() - 8, pr.center().y(), 2.4)
                p.setPen(QColor("#1a130c"))
                p.drawText(pr, Qt.AlignmentFlag.AlignCenter, self._right)
            p.end()

    class GaugeDial(QWidget):
        """Analog temperature gauge: brass bezel, banded arc, needle, peak marker, digital value."""

        MIN_C, MAX_C = 20.0, 120.0
        START_DEG, SPAN_DEG = 225.0, 270.0  # sweep clockwise from lower-left to lower-right

        def __init__(self, label: str, amber: float = 70.0, red: float = 85.0, parent=None) -> None:
            super().__init__(parent)
            self._label = label
            self._amber, self._red = amber, red
            self._value: float | None = None
            self._shown: float | None = None
            self._peak: float | None = None
            self._note = ""
            self._extra = ""
            self.setMinimumSize(230, 230)
            self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        def sizeHint(self) -> QSize:  # noqa: N802
            return QSize(280, 280)

        def hasHeightForWidth(self) -> bool:  # noqa: N802
            return False

        def set_reading(self, value: float | None, peak: float | None, note: str = "", extra: str = "") -> None:
            self._value, self._peak, self._note, self._extra = value, peak, note, extra
            self._shown = value
            self.update()

        def value(self) -> float | None:
            return self._value

        def _angle(self, t: float) -> float:
            t = max(self.MIN_C, min(self.MAX_C, t))
            frac = (t - self.MIN_C) / (self.MAX_C - self.MIN_C)
            return math.radians(self.START_DEG - frac * self.SPAN_DEG)

        def _pt(self, cx, cy, r, ang) -> QPointF:
            return QPointF(cx + r * math.cos(ang), cy - r * math.sin(ang))

        def _level_color(self, t: float | None) -> QColor:
            if t is None:
                return QColor(C["text_mute"])
            if t > self._red:
                return QColor(C["red"])
            if t >= self._amber:
                return QColor(C["amber"])
            return QColor(C["good"])

        def paintEvent(self, _ev) -> None:  # noqa: N802
            p = QPainter(self)
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
            w, h = self.width(), self.height()
            side = min(w, h) - 8
            R = side / 2
            cx, cy = w / 2, h / 2
            # bezel (outer brass ring)
            bez = QConicalGradient(QPointF(cx, cy), 120)
            for pos, col in ((0.0, "#f2d38e"), (0.18, "#9c7430"), (0.38, "#e7c57c"),
                             (0.6, "#7a5a24"), (0.8, "#d8b066"), (1.0, "#f2d38e")):
                bez.setColorAt(pos, QColor(col))
            p.setPen(QPen(QColor("#3a2810"), 1.2))
            p.setBrush(QBrush(bez))
            p.drawEllipse(QPointF(cx, cy), R, R)
            p.setBrush(QBrush(QColor("#2a1f12")))
            p.setPen(Qt.PenStyle.NoPen)
            p.drawEllipse(QPointF(cx, cy), R * 0.905, R * 0.905)
            # face
            face_r = R * 0.885
            fg = QRadialGradient(QPointF(cx, cy - face_r * 0.35), face_r * 1.25)
            fg.setColorAt(0.0, QColor("#2e271f"))
            fg.setColorAt(0.7, QColor("#18140f"))
            fg.setColorAt(1.0, QColor("#0e0b08"))
            p.setBrush(QBrush(fg))
            p.drawEllipse(QPointF(cx, cy), face_r, face_r)
            # bezel screws
            for deg in (45, 135, 225, 315):
                a = math.radians(deg)
                rivet(p, cx + R * 0.952 * math.cos(a), cy - R * 0.952 * math.sin(a), max(2.2, R * 0.022))
            # colored bands
            band_r = face_r * 0.86
            band_w = max(4.0, face_r * 0.07)
            rect = QRectF(cx - band_r, cy - band_r, band_r * 2, band_r * 2)

            def band(t0, t1, col, alpha=210):
                a0 = math.degrees(self._angle(t0))
                a1 = math.degrees(self._angle(t1))
                c = QColor(col)
                c.setAlpha(alpha)
                pen = QPen(c, band_w)
                pen.setCapStyle(Qt.PenCapStyle.FlatCap)
                p.setPen(pen)
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawArc(rect, int(a0 * 16), int((a1 - a0) * 16))

            band(self.MIN_C, self._amber, "#4f8f84", 150)
            band(self._amber, self._red, C["amber"])
            band(self._red, self.MAX_C, C["red"])
            # ticks
            for t in range(int(self.MIN_C), int(self.MAX_C) + 1, 5):
                a = self._angle(t)
                major = t % 10 == 0
                r0 = band_r - band_w / 2 - (face_r * 0.11 if major else face_r * 0.06)
                r1 = band_r - band_w / 2 - 2
                p.setPen(QPen(QColor("#e8dcc4" if major else "#8a7c62"), 2.0 if major else 1.0))
                p.drawLine(self._pt(cx, cy, r0, a), self._pt(cx, cy, r1, a))
            nf = QFont()
            nf.setPointSizeF(max(7.0, face_r * 0.072))
            nf.setWeight(QFont.Weight.DemiBold)
            p.setFont(nf)
            p.setPen(QColor("#cbbd9f"))
            for t in range(int(self.MIN_C), int(self.MAX_C) + 1, 10):
                if (t - int(self.MIN_C)) % 20:
                    continue
                a = self._angle(t)
                pt = self._pt(cx, cy, band_r - band_w / 2 - face_r * 0.22, a)
                tr = QRectF(pt.x() - 20, pt.y() - 10, 40, 20)
                p.drawText(tr, Qt.AlignmentFlag.AlignCenter, str(t))
            # label + unit (serif, engraved)
            lf = display_font(max(9.0, face_r * 0.11), 700)
            lf.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 2.0)
            p.setFont(lf)
            p.setPen(QColor("#d4a94f"))
            p.drawText(QRectF(cx - face_r, cy + face_r * 0.13, face_r * 2, face_r * 0.20),
                       Qt.AlignmentFlag.AlignCenter, self._label)
            # peak marker (brass triangle on the band)
            if self._peak is not None:
                a = self._angle(self._peak)
                tip = self._pt(cx, cy, band_r - band_w / 2 - 1, a)
                b1 = self._pt(cx, cy, band_r + band_w / 2 + face_r * 0.06, a + 0.055)
                b2 = self._pt(cx, cy, band_r + band_w / 2 + face_r * 0.06, a - 0.055)
                path = QPainterPath(tip)
                path.lineTo(b1)
                path.lineTo(b2)
                path.closeSubpath()
                p.setPen(QPen(QColor("#2a1d0a"), 0.8))
                p.setBrush(QBrush(QColor("#f0cd80")))
                p.drawPath(path)
            # digital readout window
            lcd_w, lcd_h = face_r * 0.58, face_r * 0.34
            lcd = QRectF(cx - lcd_w / 2, cy + face_r * 0.36, lcd_w, lcd_h)
            p.setPen(QPen(QColor("#7a5a24"), 1.2))
            p.setBrush(QBrush(QColor("#0b0907")))
            p.drawRoundedRect(lcd, 5, 5)
            vf = mono_font(max(10.0, face_r * 0.135))
            vf.setWeight(QFont.Weight.Bold)
            p.setFont(vf)
            col = self._level_color(self._value)
            txt = "—" if self._value is None else f"{self._value:.0f}°"
            p.setPen(col)
            p.drawText(QRectF(lcd.left(), lcd.top() + lcd_h * 0.04, lcd_w, lcd_h * 0.60), Qt.AlignmentFlag.AlignCenter, txt)
            sm = QFont()
            sm.setPointSizeF(max(6.5, face_r * 0.052))
            sm.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 1.0)
            p.setFont(sm)
            p.setPen(QColor("#a89a80"))
            sub = self._note or ("PEAK —" if self._peak is None else f"PEAK {self._peak:.0f}°")
            p.drawText(QRectF(lcd.left(), lcd.top() + lcd_h * 0.62, lcd_w, lcd_h * 0.32), Qt.AlignmentFlag.AlignCenter, sub)
            if self._extra:
                p.drawText(QRectF(cx - face_r, lcd.bottom() + face_r * 0.04, face_r * 2, face_r * 0.14),
                           Qt.AlignmentFlag.AlignCenter, self._extra)
            # needle
            if self._shown is not None:
                a = self._angle(self._shown)
                tip = self._pt(cx, cy, band_r - band_w * 0.2, a)
                tail = self._pt(cx, cy, -face_r * 0.10, a)
                perp = a + math.pi / 2
                wbase = max(2.6, face_r * 0.035)
                l = QPointF(cx + wbase * math.cos(perp), cy - wbase * math.sin(perp))
                r_ = QPointF(cx - wbase * math.cos(perp), cy + wbase * math.sin(perp))
                needle = QPainterPath(tip)
                needle.lineTo(l)
                needle.lineTo(tail)
                needle.lineTo(r_)
                needle.closeSubpath()
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QColor(0, 0, 0, 110))
                p.drawPath(needle.translated(2, 3))
                ng = QLinearGradient(tail, tip)
                ng.setColorAt(0.0, QColor("#7a3f22"))
                ng.setColorAt(0.5, QColor("#d98a5a"))
                ng.setColorAt(1.0, QColor("#f0b48a"))
                p.setPen(QPen(QColor("#3a1a0a"), 0.8))
                p.setBrush(QBrush(ng))
                p.drawPath(needle)
            # hub
            hub_r = max(5.0, face_r * 0.085)
            hg = QRadialGradient(QPointF(cx - hub_r * 0.3, cy - hub_r * 0.3), hub_r * 1.4)
            hg.setColorAt(0.0, QColor("#f6dc9c"))
            hg.setColorAt(0.5, QColor("#b5893b"))
            hg.setColorAt(1.0, QColor("#4a3412"))
            p.setPen(QPen(QColor("#2a1d0a"), 1))
            p.setBrush(QBrush(hg))
            p.drawEllipse(QPointF(cx, cy), hub_r, hub_r)
            # glass highlight
            gl = QLinearGradient(QPointF(cx, cy - face_r), QPointF(cx, cy))
            gl.setColorAt(0.0, QColor(255, 240, 210, 26))
            gl.setColorAt(1.0, QColor(255, 240, 210, 0))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QBrush(gl))
            gp = QPainterPath()
            gp.addEllipse(QPointF(cx, cy - face_r * 0.28), face_r * 0.82, face_r * 0.62)
            p.drawPath(gp)
            p.end()

    _WIDGETS = {"Plate": Plate, "HeaderBar": HeaderBar, "GaugeDial": GaugeDial}
    return _WIDGETS
