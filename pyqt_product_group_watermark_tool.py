# -*- coding: utf-8 -*-
import hashlib
import io
import json
import random
import shutil
import sys
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from PIL import Image, ImageOps
from PyQt5.QtCore import QObject, QRect, QRectF, QSize, Qt, QThread, QTimer, pyqtSignal
from PyQt5.QtGui import QColor, QIcon, QImage, QPainter, QPen, QPixmap
from PyQt5.QtWidgets import (
    QAbstractSpinBox,
    QAbstractItemView,
    QApplication,
    QListView,
    QFileDialog,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QProgressDialog,
    QSpinBox,
    QTabWidget,
    QTextEdit,
    QInputDialog,
    QMenu,
    QDialog,
    QStyledItemDelegate,
    QVBoxLayout,
    QWidget,
    QComboBox,
    QCheckBox,
    QStyle,
    QSizePolicy,
)

from features.batch_export import batch_export as feature_batch_export
from features.batch_export import refresh_export_config_list as feature_refresh_export_config_list
from features.batch_export import refresh_export_folder_list as feature_refresh_export_folder_list
from features.import_dedup import run_import_to_internal_library as feature_import_to_internal_library
from features.product_grouping import build_second_layer as feature_build_second_layer
from features.product_grouping import pick_candidates as feature_pick_candidates
from features.product_grouping import resolve_candidate_item_path as feature_resolve_candidate_item_path
from features.watermark_config import add_watermark_file as feature_add_watermark_file
from features.watermark_config import apply_wm_settings as feature_apply_wm_settings
from features.watermark_config import current_cfg_obj as feature_current_cfg_obj
from features.watermark_config import delete_selected_watermark as feature_delete_selected_watermark
from features.watermark_config import load_selected_config as feature_load_selected_config
from features.watermark_config import refresh_watermark_lists as feature_refresh_watermark_lists
from features.watermark_config import reset_wm_settings as feature_reset_wm_settings
from features.watermark_config import save_current_config as feature_save_current_config


IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff"}
MAX_IMPORT_IMAGE_BYTES = 2 * 1024 * 1024


@dataclass
class WatermarkConfig:
    name: str
    watermark_file: str
    x_pct: int = 50
    y_pct: int = 50
    scale_pct: int = 25
    rotate_deg: int = 0
    opacity_pct: int = 100


class ThumbnailWorker(QObject):
    thumbnail_ready = pyqtSignal(int, QIcon)
    finished = pyqtSignal()

    def __init__(self, files: List[Path], thumb_w: int = 176, thumb_h: int = 102) -> None:
        super().__init__()
        self.files = files
        self.thumb_w = thumb_w
        self.thumb_h = thumb_h
        self._stop = False

    def stop(self) -> None:
        self._stop = True

    def run(self) -> None:
        try:
            for row, path in enumerate(self.files):
                if self._stop:
                    break
                try:
                    with Image.open(path) as im:
                        rgb = im.convert("RGB")
                        # Keep original ratio and pad to a smaller unified canvas.
                        fitted = ImageOps.contain(
                            rgb,
                            (self.thumb_w, self.thumb_h),
                            method=Image.Resampling.LANCZOS,
                        )
                        canvas = Image.new("RGB", (self.thumb_w, self.thumb_h), (245, 245, 245))
                        x = (self.thumb_w - fitted.width) // 2
                        y = (self.thumb_h - fitted.height) // 2
                        canvas.paste(fitted, (x, y))
                    pix = pil_to_qpixmap(canvas)
                    self.thumbnail_ready.emit(row, QIcon(pix))
                except Exception:
                    self.thumbnail_ready.emit(row, QIcon())
        finally:
            self.finished.emit()


class ImagePreviewDialog(QDialog):
    def __init__(self, image_path: Path, parent=None) -> None:
        super().__init__(parent)
        self.image_path = image_path
        self.setWindowTitle(f"图片预览 - {image_path.name}")
        self.resize(1100, 760)
        self.label = QLabel()
        self.label.setAlignment(Qt.AlignCenter)
        self.label.setStyleSheet("background:#222; color:#ddd;")
        lay = QVBoxLayout(self)
        lay.addWidget(self.label)
        self._pix = QPixmap(str(image_path))
        if self._pix.isNull():
            self.label.setText("图片不存在")
        else:
            self._refresh_pixmap()

    def _refresh_pixmap(self) -> None:
        if self._pix.isNull():
            return
        scaled = self._pix.scaled(
            max(1, self.label.width() - 8),
            max(1, self.label.height() - 8),
            Qt.KeepAspectRatio,
            Qt.SmoothTransformation,
        )
        self.label.setPixmap(scaled)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._refresh_pixmap()


class GroupSortManagerDialog(QDialog):
    def __init__(
        self,
        current_order: List[str],
        default_order: List[str],
        count_map: Dict[str, int],
        ctime_map: Dict[str, float],
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("图片排序管理")
        self.resize(420, 520)
        self.count_map = count_map
        self.ctime_map = ctime_map
        self.list_widget = QListWidget()
        self.list_widget.setDragDropMode(QListWidget.InternalMove)
        self.list_widget.setDefaultDropAction(Qt.MoveAction)
        for n in current_order:
            self.list_widget.addItem(n)
        if self.list_widget.count() > 0:
            self.list_widget.setCurrentRow(0)

        btn_up = QPushButton("上移")
        btn_down = QPushButton("下移")
        btn_top = QPushButton("置顶")
        btn_bottom = QPushButton("置底")
        btn_count_asc = QPushButton("数量升序")
        btn_count_desc = QPushButton("数量降序")
        btn_reset = QPushButton("重置")
        btn_ok = QPushButton("确定")
        btn_cancel = QPushButton("取消")

        btn_up.clicked.connect(lambda: self._move_current(-1))
        btn_down.clicked.connect(lambda: self._move_current(1))
        btn_top.clicked.connect(self._move_top)
        btn_bottom.clicked.connect(self._move_bottom)
        btn_count_asc.clicked.connect(lambda: self._sort_by_count(False))
        btn_count_desc.clicked.connect(lambda: self._sort_by_count(True))
        btn_reset.clicked.connect(lambda: self._reset(default_order))
        btn_ok.clicked.connect(self.accept)
        btn_cancel.clicked.connect(self.reject)

        row1 = QHBoxLayout()
        row1.setContentsMargins(0, 0, 0, 0)
        row1.setSpacing(8)
        row1.addWidget(btn_up)
        row1.addWidget(btn_down)
        row1.addWidget(btn_top)
        row1.addWidget(btn_bottom)

        row2 = QHBoxLayout()
        row2.setContentsMargins(0, 0, 0, 0)
        row2.setSpacing(8)
        row2.addWidget(btn_count_asc)
        row2.addWidget(btn_count_desc)
        row2.addStretch(1)

        row3 = QHBoxLayout()
        row3.setContentsMargins(0, 0, 0, 0)
        row3.setSpacing(8)
        row2.addWidget(btn_reset)
        row3.addStretch(1)
        row3.addWidget(btn_ok)
        row3.addWidget(btn_cancel)

        lay = QVBoxLayout(self)
        lay.addWidget(self.list_widget)
        lay.addLayout(row1)
        lay.addLayout(row2)
        lay.addLayout(row3)

    def _move_current(self, delta: int) -> None:
        row = self.list_widget.currentRow()
        if row < 0:
            return
        target = row + delta
        if target < 0 or target >= self.list_widget.count():
            return
        item = self.list_widget.takeItem(row)
        self.list_widget.insertItem(target, item)
        self.list_widget.setCurrentRow(target)

    def _move_top(self) -> None:
        row = self.list_widget.currentRow()
        if row <= 0:
            return
        item = self.list_widget.takeItem(row)
        self.list_widget.insertItem(0, item)
        self.list_widget.setCurrentRow(0)

    def _move_bottom(self) -> None:
        row = self.list_widget.currentRow()
        if row < 0 or row >= self.list_widget.count() - 1:
            return
        item = self.list_widget.takeItem(row)
        self.list_widget.addItem(item)
        self.list_widget.setCurrentRow(self.list_widget.count() - 1)

    def _reset(self, default_order: List[str]) -> None:
        self.list_widget.clear()
        for n in default_order:
            self.list_widget.addItem(n)
        if self.list_widget.count() > 0:
            self.list_widget.setCurrentRow(0)

    def _sort_by_count(self, desc: bool) -> None:
        names = self.get_order()
        names.sort(
            key=lambda n: (
                -self.count_map.get(n, 0) if desc else self.count_map.get(n, 0),
                self.ctime_map.get(n, 0.0),
                n.lower(),
            )
        )
        self._reset(names)

    def get_order(self) -> List[str]:
        return [self.list_widget.item(i).text() for i in range(self.list_widget.count())]


class WatermarkComboDelegate(QStyledItemDelegate):
    def paint(self, painter, option, index):
        painter.save()
        rect = option.rect
        if option.state & QStyle.State_Selected:
            painter.fillRect(rect, option.palette.highlight())
            text_color = option.palette.highlightedText().color()
        else:
            painter.fillRect(rect, option.palette.base())
            text_color = option.palette.text().color()

        icon = index.data(Qt.DecorationRole)
        text = str(index.data(Qt.DisplayRole) or "")

        icon_size = 44
        top_margin = 4
        icon_x = rect.x() + (rect.width() - icon_size) // 2
        icon_y = rect.y() + top_margin
        if isinstance(icon, QIcon) and not icon.isNull():
            icon.paint(painter, QRect(icon_x, icon_y, icon_size, icon_size), Qt.AlignCenter)

        painter.setPen(text_color)
        text_rect = QRect(rect.x() + 4, rect.y() + top_margin + icon_size + 4, rect.width() - 8, rect.height() - icon_size - 8)
        painter.drawText(text_rect, Qt.AlignHCenter | Qt.AlignTop, text)
        painter.restore()

    def sizeHint(self, option, index):
        _ = option, index
        return QSize(92, 68)


class OverlayPreviewLabel(QLabel):
    borderChanged = pyqtSignal(object)
    overlayDragged = pyqtSignal(float, float)

    def __init__(self, text: str = "", parent=None) -> None:
        super().__init__(text, parent)
        self.setMouseTracking(True)
        self._actual_rect_norm: Optional[Tuple[float, float, float, float]] = None
        self._current_rect_norm: Optional[Tuple[float, float, float, float]] = None
        self._show_frames = False
        self._manual_mode = False
        self._drag_edge: Optional[str] = None
        self._drag_move = False
        self._drag_last_x = 0
        self._drag_last_y = 0

    def set_overlay_rects(
        self,
        actual_rect_norm: Optional[Tuple[float, float, float, float]],
        current_rect_norm: Optional[Tuple[float, float, float, float]],
        show_frames: bool,
        manual_mode: bool,
    ) -> None:
        self._actual_rect_norm = actual_rect_norm
        self._current_rect_norm = current_rect_norm
        self._show_frames = show_frames
        self._manual_mode = manual_mode
        self.update()

    def _pix_rect(self) -> Optional[QRect]:
        p = self.pixmap()
        if p is None or p.isNull():
            return None
        x = (self.width() - p.width()) // 2
        y = (self.height() - p.height()) // 2
        return QRect(x, y, p.width(), p.height())

    @staticmethod
    def _norm_to_rect(norm: Tuple[float, float, float, float], base: QRect) -> QRectF:
        x, y, w, h = norm
        return QRectF(
            base.x() + x * base.width(),
            base.y() + y * base.height(),
            max(1.0, w * base.width()),
            max(1.0, h * base.height()),
        )

    @staticmethod
    def _clamp_rect_norm(rect: Tuple[float, float, float, float]) -> Tuple[float, float, float, float]:
        x, y, w, h = rect
        x = max(0.0, min(1.0, x))
        y = max(0.0, min(1.0, y))
        w = max(0.01, min(1.0 - x, w))
        h = max(0.01, min(1.0 - y, h))
        return (x, y, w, h)

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        if not self._show_frames:
            return
        p_rect = self._pix_rect()
        if p_rect is None:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, False)
        if self._actual_rect_norm is not None:
            r = self._norm_to_rect(self._actual_rect_norm, p_rect)
            painter.setPen(QPen(QColor(60, 220, 120), 2))
            painter.drawRect(r)
        if self._current_rect_norm is not None:
            r = self._norm_to_rect(self._current_rect_norm, p_rect)
            pen = QPen(QColor(255, 196, 0), 2)
            pen.setStyle(Qt.DashLine)
            painter.setPen(pen)
            painter.drawRect(r)
        painter.end()

    def _detect_edge(self, x: float, y: float, r: QRectF) -> Optional[str]:
        tol = 7.0
        near_l = abs(x - r.left()) <= tol and (r.top() - tol) <= y <= (r.bottom() + tol)
        near_r = abs(x - r.right()) <= tol and (r.top() - tol) <= y <= (r.bottom() + tol)
        near_t = abs(y - r.top()) <= tol and (r.left() - tol) <= x <= (r.right() + tol)
        near_b = abs(y - r.bottom()) <= tol and (r.left() - tol) <= x <= (r.right() + tol)
        if near_l:
            return "left"
        if near_r:
            return "right"
        if near_t:
            return "top"
        if near_b:
            return "bottom"
        return None

    def mousePressEvent(self, event) -> None:
        if (
            event.button() == Qt.LeftButton
            and self._manual_mode
            and self._current_rect_norm is not None
        ):
            p_rect = self._pix_rect()
            if p_rect is not None:
                cr = self._norm_to_rect(self._current_rect_norm, p_rect)
                if cr.contains(event.x(), event.y()):
                    self._drag_move = True
                    self._drag_last_x = event.x()
                    self._drag_last_y = event.y()
                    event.accept()
                    return
        if (
            event.button() == Qt.LeftButton
            and self._show_frames
            and self._manual_mode
            and self._current_rect_norm is not None
        ):
            p_rect = self._pix_rect()
            if p_rect is not None:
                cr = self._norm_to_rect(self._current_rect_norm, p_rect)
                edge = self._detect_edge(event.x(), event.y(), cr)
                if edge is not None:
                    self._drag_edge = edge
                    event.accept()
                    return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self._drag_move:
            p_rect = self._pix_rect()
            if p_rect is None or p_rect.width() <= 0 or p_rect.height() <= 0:
                return
            dx_px = event.x() - self._drag_last_x
            dy_px = event.y() - self._drag_last_y
            self._drag_last_x = event.x()
            self._drag_last_y = event.y()
            self.overlayDragged.emit(dx_px / float(p_rect.width()), dy_px / float(p_rect.height()))
            event.accept()
            return
        if self._drag_edge and self._current_rect_norm is not None:
            p_rect = self._pix_rect()
            if p_rect is None or p_rect.width() <= 0 or p_rect.height() <= 0:
                return
            x, y, w, h = self._current_rect_norm
            nx = (event.x() - p_rect.x()) / float(p_rect.width())
            ny = (event.y() - p_rect.y()) / float(p_rect.height())
            min_size = 0.01
            if self._drag_edge == "left":
                right = x + w
                x = max(0.0, min(nx, right - min_size))
                w = right - x
            elif self._drag_edge == "right":
                w = max(min_size, min(1.0 - x, nx - x))
            elif self._drag_edge == "top":
                bottom = y + h
                y = max(0.0, min(ny, bottom - min_size))
                h = bottom - y
            elif self._drag_edge == "bottom":
                h = max(min_size, min(1.0 - y, ny - y))
            self._current_rect_norm = self._clamp_rect_norm((x, y, w, h))
            self.borderChanged.emit(self._current_rect_norm)
            self.update()
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        self._drag_move = False
        self._drag_edge = None
        super().mouseReleaseEvent(event)


def ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def load_json(path: Path, default):
    if not path.exists():
        return default
    try:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def save_json(path: Path, obj) -> None:
    ensure_dir(path.parent)
    with path.open("w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)


def is_image_file(path: Path) -> bool:
    return path.is_file() and path.suffix.lower() in IMAGE_EXTS


def md5_of_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    m = hashlib.md5()
    with path.open("rb") as f:
        while True:
            chunk = f.read(chunk_size)
            if not chunk:
                break
            m.update(chunk)
    return m.hexdigest()


def _encode_jpg_bytes(img: Image.Image, quality: int) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=quality, optimize=True, progressive=True)
    return buf.getvalue()


def compress_image_to_under_2mb(src: Path, dst: Path, max_bytes: int = MAX_IMPORT_IMAGE_BYTES) -> Tuple[bool, int]:
    src_size = src.stat().st_size
    if src_size <= max_bytes:
        shutil.copy2(src, dst)
        return False, src_size

    with Image.open(src) as im:
        if im.mode in ("RGBA", "LA"):
            bg = Image.new("RGB", im.size, (255, 255, 255))
            bg.paste(im, mask=im.split()[-1])
            img = bg
        else:
            img = im.convert("RGB")

    def best_quality_under_limit(candidate: Image.Image, q_low: int, q_high: int) -> Optional[bytes]:
        # Find the highest JPEG quality that stays below the size limit.
        ok_bytes = None
        lo, hi = q_low, q_high
        while lo <= hi:
            mid = (lo + hi) // 2
            b = _encode_jpg_bytes(candidate, mid)
            if len(b) <= max_bytes:
                ok_bytes = b
                lo = mid + 1
            else:
                hi = mid - 1
        return ok_bytes

    # Prefer minimal quality impact first.
    best = best_quality_under_limit(img, 85, 96)
    if best is None:
        best = best_quality_under_limit(img, 70, 84)

    # Only perform mild downscale if quality reduction alone cannot reach target.
    if best is None:
        w, h = img.size
        for scale in [0.97, 0.94, 0.91, 0.88]:
            nw, nh = max(1, int(w * scale)), max(1, int(h * scale))
            resized = img.resize((nw, nh), Image.Resampling.LANCZOS)
            best = best_quality_under_limit(resized, 75, 95)
            if best is not None:
                break

    if best is None:
        raise RuntimeError(f"鍘嬬缉澶辫触: {src.name}")
    dst.write_bytes(best)
    return True, len(best)


def next_folder_index(parent: Path, base_name: str) -> int:
    max_idx = 0
    for p in parent.iterdir():
        if not p.is_dir():
            continue
        if len(p.name) < len(base_name) + 3:
            continue
        if not p.name.startswith(base_name):
            continue
        suffix = p.name[-3:]
        if suffix.isdigit():
            max_idx = max(max_idx, int(suffix))
    return max_idx + 1


def list_images(folder: Path) -> List[Path]:
    return [p for p in sorted(folder.rglob("*")) if is_image_file(p)]


def next_image_index_in_folder(folder: Path, base_name: str) -> int:
    max_idx = 0
    for p in folder.iterdir():
        if not is_image_file(p):
            continue
        stem = p.stem
        if not stem.startswith(base_name):
            continue
        suffix = stem[len(base_name) :]
        if len(suffix) == 3 and suffix.isdigit():
            max_idx = max(max_idx, int(suffix))
    return max_idx + 1


def pil_to_qpixmap(img: Image.Image) -> QPixmap:
    rgba = img.convert("RGBA")
    w, h = rgba.size
    data = rgba.tobytes("raw", "RGBA")
    qimg = QImage(data, w, h, 4 * w, QImage.Format_RGBA8888)
    # copy() ensures QImage owns the data buffer after function returns.
    return QPixmap.fromImage(qimg.copy())


def apply_watermark(img: Image.Image, wm: Image.Image, cfg: WatermarkConfig) -> Image.Image:
    base = img.convert("RGBA")
    mark = wm.convert("RGBA")

    target_w = max(8, int(base.width * cfg.scale_pct / 100))
    ratio = target_w / mark.width
    target_h = max(8, int(mark.height * ratio))
    mark = mark.resize((target_w, target_h), Image.Resampling.LANCZOS)
    mark = mark.rotate(cfg.rotate_deg, expand=True, resample=Image.Resampling.BICUBIC)

    if cfg.opacity_pct < 100:
        alpha = mark.getchannel("A").point(lambda x: int(x * cfg.opacity_pct / 100))
        mark.putalpha(alpha)

    cx = int(base.width * cfg.x_pct / 100)
    cy = int(base.height * cfg.y_pct / 100)
    x = cx - mark.width // 2
    y = cy - mark.height // 2

    layer = Image.new("RGBA", base.size, (0, 0, 0, 0))
    layer.paste(mark, (x, y), mark)
    out = Image.alpha_composite(base, layer)
    return out.convert("RGB")


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("图片批量加水印工具")
        self.resize(1200, 860)

        self.root = Path(__file__).resolve().parent
        self.internal_root = self.root / "internal_library"
        self.second_root = self.root / "second_layer"
        self.export_root = self.root / "export_images"
        self.watermark_root = self.root / "watermark_library" / "images"
        self.config_root = self.root / "watermark_library" / "configs"
        self.index_path = self.root / "internal_library" / "md5_index.json"
        self.settings_path = self.root / "app_settings.json"

        for p in [self.internal_root, self.second_root, self.export_root, self.watermark_root, self.config_root]:
            ensure_dir(p)

        self.md5_index: Dict[str, str] = load_json(self.index_path, {})
        self.app_settings: Dict[str, object] = load_json(self.settings_path, {})
        self.candidate_images: List[Path] = []
        self.current_internal_group_dir: Optional[Path] = None
        self._pending_group_dir: Optional[Path] = None
        self.thumb_w = 176
        self.thumb_h = 102
        self.thumb_grid_w = 190
        self.thumb_grid_h = 148
        self._thumb_worker_thread: Optional[QThread] = None
        self._thumb_worker: Optional[ThumbnailWorker] = None
        self._group_switch_timer = QTimer(self)
        self._group_switch_timer.setSingleShot(True)
        self._group_switch_timer.timeout.connect(self._apply_pending_group_change)
        self._preview_debounce_timer = QTimer(self)
        self._preview_debounce_timer.setSingleShot(True)
        self._preview_debounce_timer.timeout.connect(self.update_preview)
        self._preview_interval_normal_ms = 16
        self._preview_interval_drag_ms = 33
        self._preview_rendering = False
        self._preview_dirty = False
        self._sample_preview_cache_key: Optional[Tuple[str, float, int, int]] = None
        self._sample_preview_cache_img: Optional[Image.Image] = None
        self._wm_raw_cache_key: Optional[Tuple[str, float]] = None
        self._wm_raw_cache_img: Optional[Image.Image] = None
        self._wm_sim_cache_key: Optional[Tuple[object, ...]] = None
        self._wm_sim_cache_img: Optional[Image.Image] = None
        self._wm_preview_pix_cache_key: Optional[Tuple[object, ...]] = None
        self._wm_preview_pix_cache_img: Optional[QPixmap] = None
        self._wm_preview_pix_cache_bbox_norm: Optional[Tuple[float, float, float, float]] = None
        self._preview_sliders: List[QSpinBox] = []
        self._wm_actual_border_norm: Optional[Tuple[float, float, float, float]] = None
        self._wm_current_border_norm: Optional[Tuple[float, float, float, float]] = None
        self._fx_actual_border_norm: Optional[Tuple[float, float, float, float]] = None
        self._fx_current_border_norm: Optional[Tuple[float, float, float, float]] = None
        self._wm_border_user_modified = False
        self._fx_border_user_modified = False
        self._preview_windows: List[ImagePreviewDialog] = []

        self.tabs = QTabWidget()
        self.setCentralWidget(self.tabs)

        self.import_tab = QWidget()
        self.group_tab = QWidget()
        self.wm_tab = QWidget()
        self.export_tab = QWidget()

        self.tabs.addTab(self.import_tab, "1) 导入图片")
        self.tabs.addTab(self.wm_tab, "2) 添加水印")
        self.tabs.addTab(self.group_tab, "3) 分组管理")
        self.tabs.addTab(self.export_tab, "4) 导出图片")
        self.tabs.currentChanged.connect(self.on_tab_changed)

        self._build_import_tab()
        self._build_group_tab()
        self._build_wm_tab()
        self._build_export_tab()
        self.refresh_watermark_lists()
        # Force initial preview render: show base image even without watermark.
        self.update_preview()

    def on_tab_changed(self, idx: int) -> None:
        # Refresh preview whenever switching to the watermark tab.
        if self.tabs.widget(idx) is self.wm_tab:
            self.update_preview()
        else:
            self._preview_debounce_timer.stop()

    def _log(self, box: QTextEdit, msg: str) -> None:
        box.append(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}")
        QApplication.processEvents()

    def _pick_dir(self, line: QLineEdit) -> None:
        d = QFileDialog.getExistingDirectory(self, "选择导入文件夹", str(self.root))
        if d:
            line.setText(d)

    def _pick_file(self, line: QLineEdit, flt: str) -> None:
        p, _ = QFileDialog.getOpenFileName(self, "选择文件", str(self.root), flt)
        if p:
            line.setText(p)

    def _normalize_row_spacing(self, row_layout: QHBoxLayout) -> None:
        # Keep row spacing consistent across the app without changing overall layout structure.
        row_layout.setContentsMargins(0, 0, 0, 0)
        row_layout.setSpacing(8)

    def _build_import_tab(self) -> None:
        layout = QVBoxLayout(self.import_tab)

        form = QFormLayout()
        self.src_import_line = QLineEdit(str(self.root / "input_images"))
        pick_btn = QPushButton("选择导入文件夹")
        pick_btn.clicked.connect(lambda: self._pick_dir(self.src_import_line))
        row = QHBoxLayout()
        self._normalize_row_spacing(row)
        row.addWidget(self.src_import_line)
        row.addWidget(pick_btn)
        w = QWidget()
        w.setLayout(row)
        form.addRow("选择导入文件夹", w)
        layout.addLayout(form)

        btn = QPushButton("导入")
        btn.clicked.connect(self.import_to_internal_library)
        layout.addWidget(btn)

        group_row = QHBoxLayout()
        left_col = QVBoxLayout()
        left_col.addWidget(QLabel("当前分组"))
        self.internal_group_stats_label = QLabel("分组数量: 0  |  图片数量: 0")
        left_col.addWidget(self.internal_group_stats_label)
        self.internal_group_list = QListWidget()
        self.internal_group_list.currentItemChanged.connect(self.on_internal_group_changed)
        self.internal_group_list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.internal_group_list.customContextMenuRequested.connect(self.on_internal_group_context_menu)
        self.internal_group_list.setMinimumHeight(420)
        self.internal_group_list.setMaximumWidth(280)
        left_col.addWidget(self.internal_group_list)
        left_wrap = QWidget()
        left_wrap.setLayout(left_col)
        group_row.addWidget(left_wrap, 0)

        mid_col = QVBoxLayout()
        mid_col.addWidget(QLabel("分组管理"))
        mode_row = QHBoxLayout()
        self._normalize_row_spacing(mode_row)
        mode_row.addWidget(QLabel("文件视图模式"))
        self.internal_file_view_mode = QComboBox()
        self.internal_file_view_mode.addItems(["列表视图", "缩略图视图"])
        self.internal_file_view_mode.currentIndexChanged.connect(self.on_internal_file_view_mode_changed)
        last_mode = self.app_settings.get("internal_file_view_mode", "缩略图视图")
        idx = self.internal_file_view_mode.findText(last_mode)
        if idx >= 0:
            self.internal_file_view_mode.setCurrentIndex(idx)
        mode_row.addWidget(self.internal_file_view_mode)
        self.btn_file_select_all = QPushButton("全选")
        self.btn_file_select_all.clicked.connect(self.select_all_files)
        mode_row.addWidget(self.btn_file_select_all)
        self.btn_file_select_none = QPushButton("取消选择")
        self.btn_file_select_none.clicked.connect(self.clear_file_selection)
        mode_row.addWidget(self.btn_file_select_none)
        self.btn_batch_move = QPushButton("移动到其他分组")
        self.btn_batch_move.clicked.connect(self.batch_move_files)
        mode_row.addWidget(self.btn_batch_move)
        self.btn_batch_delete = QPushButton("删除")
        self.btn_batch_delete.clicked.connect(self.batch_delete_files)
        mode_row.addWidget(self.btn_batch_delete)
        mode_row.addStretch(1)
        mode_wrap = QWidget()
        mode_wrap.setLayout(mode_row)
        mid_col.addWidget(mode_wrap)
        self.internal_group_file_list = QListWidget()
        self.internal_group_file_list.setMinimumHeight(420)
        self.internal_group_file_list.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.internal_group_file_list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.internal_group_file_list.customContextMenuRequested.connect(self.on_internal_file_context_menu)
        self.internal_group_file_list.itemDoubleClicked.connect(self.on_internal_file_double_clicked)
        self.internal_group_file_list.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.internal_group_file_list.setHorizontalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.internal_group_file_list.setUniformItemSizes(True)
        self.internal_group_file_list.verticalScrollBar().setSingleStep(12)
        mid_col.addWidget(self.internal_group_file_list)
        mid_wrap = QWidget()
        mid_wrap.setLayout(mid_col)
        group_row.addWidget(mid_wrap, 1)

        layout.addLayout(group_row, 1)

        log_row = QVBoxLayout()
        log_row.addWidget(QLabel("日志"))
        self.import_log = QTextEdit()
        self.import_log.setReadOnly(True)
        self.import_log.setMaximumHeight(150)
        log_row.addWidget(self.import_log)
        layout.addLayout(log_row, 0)

        self.refresh_internal_group_list()

    def _collect_internal_group_info(self) -> Tuple[List[Tuple[Path, int]], int]:
        groups: List[Tuple[Path, int]] = []
        total_images = 0
        for d in self._get_ordered_internal_group_dirs():
            cnt = len([p for p in d.iterdir() if is_image_file(p)])
            groups.append((d, cnt))
            total_images += cnt
        return groups, total_images

    def _group_ctime(self, d: Path) -> float:
        try:
            return d.stat().st_ctime
        except Exception:
            return 0.0

    def _default_internal_group_order(self) -> List[Path]:
        if not self.internal_root.exists():
            return []
        dirs = [d for d in self.internal_root.iterdir() if d.is_dir()]
        return sorted(dirs, key=lambda p: (self._group_ctime(p), p.name.lower()))

    def _is_empty_group(self, group_dir: Path) -> bool:
        if not group_dir.exists() or not group_dir.is_dir():
            return False
        has_images = any(is_image_file(p) for p in group_dir.iterdir() if p.is_file())
        has_subdirs = any(p.is_dir() for p in group_dir.iterdir())
        has_other_files = any(p.is_file() for p in group_dir.iterdir() if not is_image_file(p))
        return (not has_images) and (not has_subdirs) and (not has_other_files)

    def _group_count_map(self) -> Dict[str, int]:
        out: Dict[str, int] = {}
        if not self.internal_root.exists():
            return out
        for d in self.internal_root.iterdir():
            if not d.is_dir():
                continue
            out[d.name] = len([p for p in d.iterdir() if is_image_file(p)])
        return out

    def _group_ctime_map(self) -> Dict[str, float]:
        out: Dict[str, float] = {}
        if not self.internal_root.exists():
            return out
        for d in self.internal_root.iterdir():
            if not d.is_dir():
                continue
            out[d.name] = self._group_ctime(d)
        return out

    def _get_custom_group_order(self) -> List[str]:
        val = self.app_settings.get("internal_group_order", [])
        if isinstance(val, list):
            return [str(x) for x in val if isinstance(x, str)]
        return []

    def _set_custom_group_order(self, order: List[str]) -> None:
        self.app_settings["internal_group_order"] = order
        save_json(self.settings_path, self.app_settings)

    def _get_ordered_internal_group_dirs(self) -> List[Path]:
        default_dirs = self._default_internal_group_order()
        name_to_dir = {d.name: d for d in default_dirs}
        custom_names = [n for n in self._get_custom_group_order() if n in name_to_dir]
        custom_set = set(custom_names)
        ordered = [name_to_dir[n] for n in custom_names] + [d for d in default_dirs if d.name not in custom_set]
        normalized = [d.name for d in ordered]
        if normalized != self._get_custom_group_order():
            self._set_custom_group_order(normalized)
        return ordered

    def move_group_order(self, group_name: str, action: str) -> None:
        order = [d.name for d in self._get_ordered_internal_group_dirs()]
        if group_name not in order:
            return
        i = order.index(group_name)
        if action == "up" and i > 0:
            order[i - 1], order[i] = order[i], order[i - 1]
        elif action == "down" and i < len(order) - 1:
            order[i + 1], order[i] = order[i], order[i + 1]
        elif action == "top" and i > 0:
            order.insert(0, order.pop(i))
        elif action == "bottom" and i < len(order) - 1:
            order.append(order.pop(i))
        else:
            return
        self._set_custom_group_order(order)
        self.refresh_internal_group_list()
        self.select_internal_group(self.internal_root / group_name)

    def open_group_sort_manager(self) -> None:
        current = [d.name for d in self._get_ordered_internal_group_dirs()]
        default = [d.name for d in self._default_internal_group_order()]
        if not current:
            QMessageBox.information(self, "提示", "当前没有分组")
            return
        dlg = GroupSortManagerDialog(current, default, self._group_count_map(), self._group_ctime_map(), self)
        if dlg.exec_() != QDialog.Accepted:
            return
        order = dlg.get_order()
        self._set_custom_group_order(order)
        self.refresh_internal_group_list()

    def _update_internal_group_stats(self, group_count: int, total_images: int) -> None:
        self.internal_group_stats_label.setText(f"分组数量: {group_count}  |  图片数量: {total_images}")
        QApplication.processEvents()

    def refresh_internal_group_overview(self) -> None:
        # Lightweight refresh for import stage: update left grouping panel and stats.
        selected_dir = None
        cur = self.internal_group_list.currentItem()
        if cur is not None:
            raw = cur.data(Qt.UserRole)
            if raw:
                selected_dir = str(raw)

        groups, total_images = self._collect_internal_group_info()
        self.internal_group_list.blockSignals(True)
        self.internal_group_list.clear()
        for d, cnt in groups:
            it = QListWidgetItem(f"{d.name} ({cnt} 图片)")
            it.setData(Qt.UserRole, str(d))
            self.internal_group_list.addItem(it)
        if selected_dir:
            for i in range(self.internal_group_list.count()):
                it = self.internal_group_list.item(i)
                if it and str(it.data(Qt.UserRole)) == selected_dir:
                    self.internal_group_list.setCurrentRow(i)
                    break
        elif self.internal_group_list.count() > 0:
            self.internal_group_list.setCurrentRow(0)
        self.internal_group_list.blockSignals(False)
        self._update_internal_group_stats(len(groups), total_images)

    def refresh_internal_group_list(self) -> None:
        self._stop_thumbnail_worker()
        self.internal_group_list.clear()
        self.internal_group_file_list.clear()
        self.current_internal_group_dir = None
        groups, total_images = self._collect_internal_group_info()
        for d, cnt in groups:
            it = QListWidgetItem(f"{d.name} ({cnt} 图片)")
            it.setData(Qt.UserRole, str(d))
            self.internal_group_list.addItem(it)
        if self.internal_group_list.count() > 0:
            self.internal_group_list.setCurrentRow(0)
        self._update_internal_group_stats(len(groups), total_images)

    def _remap_md5_index_paths(self, old_prefix: Path, new_prefix: Path) -> None:
        old_norm = str(old_prefix.relative_to(self.root)).replace("\\", "/")
        new_norm = str(new_prefix.relative_to(self.root)).replace("\\", "/")
        if not old_norm.endswith("/"):
            old_norm += "/"
        if not new_norm.endswith("/"):
            new_norm += "/"

        remapped: Dict[str, str] = {}
        for k, v in self.md5_index.items():
            vv = str(v).replace("\\", "/")
            if vv.startswith(old_norm):
                vv = new_norm + vv[len(old_norm) :]
            remapped[k] = vv.replace("/", "\\")
        self.md5_index = remapped
        save_json(self.index_path, self.md5_index)

    def on_internal_group_context_menu(self, pos) -> None:
        item = self.internal_group_list.itemAt(pos)
        if item is None:
            menu = QMenu(self)
            act_create = menu.addAction("创建分组")
            act_clear_empty = menu.addAction("清除空分组")
            act_sort_mgr = menu.addAction("排序管理")
            chosen = menu.exec_(self.internal_group_list.mapToGlobal(pos))
            if chosen == act_create:
                self.create_internal_group()
            if chosen == act_clear_empty:
                self.clear_empty_groups()
            if chosen == act_sort_mgr:
                self.open_group_sort_manager()
            return
        raw = item.data(Qt.UserRole)
        if not raw:
            return
        group_dir = Path(str(raw))
        if not group_dir.exists() or not group_dir.is_dir():
            return

        menu = QMenu(self)
        act_rename = menu.addAction("重命名")
        act_delete = menu.addAction("删除")
        act_clear_empty = menu.addAction("清除空分组") if self._is_empty_group(group_dir) else None
        menu.addSeparator()
        act_move_up = menu.addAction("上移")
        act_move_down = menu.addAction("下移")
        act_move_top = menu.addAction("置顶")
        act_move_bottom = menu.addAction("置底")
        act_sort_mgr = menu.addAction("排序管理")
        chosen = menu.exec_(self.internal_group_list.mapToGlobal(pos))
        if chosen is None:
            return
        if chosen == act_rename:
            self.rename_internal_group(group_dir)
            return
        if chosen == act_delete:
            self.delete_internal_group(group_dir)
            return
        if act_clear_empty is not None and chosen == act_clear_empty:
            self.clear_empty_groups()
            return
        if chosen == act_move_up:
            self.move_group_order(group_dir.name, "up")
            return
        if chosen == act_move_down:
            self.move_group_order(group_dir.name, "down")
            return
        if chosen == act_move_top:
            self.move_group_order(group_dir.name, "top")
            return
        if chosen == act_move_bottom:
            self.move_group_order(group_dir.name, "bottom")
            return
        if chosen == act_sort_mgr:
            self.open_group_sort_manager()
            return

    def create_internal_group(self) -> None:
        name, ok = QInputDialog.getText(self, "创建分组", "请输入分组名称")
        if not ok:
            return
        name = name.strip()
        if not name:
            QMessageBox.warning(self, "提示", "分组名称不能为空")
            return
        invalid = set('<>:"/\\|?*')
        if any(ch in invalid for ch in name):
            QMessageBox.warning(self, "提示", "分组名称不能包含特殊字符")
            return
        target = self.internal_root / name
        if target.exists():
            QMessageBox.warning(self, "提示", f"分组名称已存在: {name}")
            return
        ensure_dir(target)
        cur_order = self._get_custom_group_order()
        if name not in cur_order:
            cur_order.append(name)
            self._set_custom_group_order(cur_order)
        self._log(self.import_log, f"创建分组: {name}")
        self.refresh_internal_group_list()
        self.select_internal_group(target)

    def clear_empty_groups(self) -> None:
        groups = [d for d in sorted(self.internal_root.iterdir()) if d.is_dir()]
        empty_groups = []
        for d in groups:
            has_images = any(is_image_file(p) for p in d.iterdir() if p.is_file())
            has_subdirs = any(p.is_dir() for p in d.iterdir())
            has_other_files = any(p.is_file() for p in d.iterdir() if not is_image_file(p))
            if not has_images and not has_subdirs and not has_other_files:
                empty_groups.append(d)
        if not empty_groups:
            QMessageBox.information(self, "提示", "当前没有空分组")
            return
        ans = QMessageBox.question(
            self,
            "清除空分组",
            f"确定清除 {len(empty_groups)} 个空分组吗？",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if ans != QMessageBox.Yes:
            return
        removed = 0
        for d in empty_groups:
            try:
                d.rmdir()
                removed += 1
            except Exception:
                continue
        self._log(self.import_log, f"清除空分组: {removed} 个")
        self.refresh_internal_group_list()

    def select_internal_group(self, group_dir: Path) -> None:
        for i in range(self.internal_group_list.count()):
            it = self.internal_group_list.item(i)
            if it and str(it.data(Qt.UserRole)) == str(group_dir):
                self.internal_group_list.setCurrentRow(i)
                return

    def rename_internal_group(self, old_dir: Path) -> None:
        old_name = old_dir.name
        new_name, ok = QInputDialog.getText(self, "重命名", "请输入新的分组名称", text=old_name)
        if not ok:
            return
        new_name = new_name.strip()
        if not new_name:
            QMessageBox.warning(self, "提示", "分组名称不能为空")
            return
        invalid = set('<>:"/\\|?*')
        if any(ch in invalid for ch in new_name):
            QMessageBox.warning(self, "提示", "分组名称不能包含特殊字符")
            return
        if new_name == old_name:
            return

        new_dir = old_dir.parent / new_name
        if new_dir.exists():
            QMessageBox.warning(self, "提示", f"分组名称已存在: {new_name}")
            return

        self._stop_thumbnail_worker()
        self._group_switch_timer.stop()
        try:
            old_dir.rename(new_dir)
            imgs = [p for p in sorted(new_dir.iterdir()) if is_image_file(p)]

            # Two-phase rename to avoid collisions.
            temp_files: List[Path] = []
            for i, p in enumerate(imgs, 1):
                tmp = new_dir / f"__tmp_rename__{i:04d}{p.suffix.lower()}"
                p.rename(tmp)
                temp_files.append(tmp)
            for i, tmp in enumerate(temp_files, 1):
                final = new_dir / f"{new_name}{i:03d}{tmp.suffix.lower()}"
                tmp.rename(final)

            self._remap_md5_index_paths(old_dir, new_dir)
            order = self._get_custom_group_order()
            if old_name in order:
                order[order.index(old_name)] = new_name
                self._set_custom_group_order(order)
            self._log(self.import_log, f"重命名: {old_name} -> {new_name} 修改了 {len(temp_files)} 个文件")
            self.refresh_internal_group_list()
            self.select_internal_group(new_dir)
        except Exception as e:
            QMessageBox.critical(self, "错误", f"{type(e).__name__}: {e}")

    def delete_internal_group(self, group_dir: Path) -> None:
        name = group_dir.name
        ans = QMessageBox.question(
            self,
            "删除分组",
            f"确定删除 {name} 分组吗？将会删除该分组及其所有图片",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if ans != QMessageBox.Yes:
            return

        self._stop_thumbnail_worker()
        self._group_switch_timer.stop()
        try:
            shutil.rmtree(group_dir)
            prefix = str(group_dir.relative_to(self.root)).replace("\\", "/") + "/"
            self.md5_index = {
                k: v
                for k, v in self.md5_index.items()
                if not str(v).replace("\\", "/").startswith(prefix)
            }
            save_json(self.index_path, self.md5_index)
            order = [n for n in self._get_custom_group_order() if n != name]
            self._set_custom_group_order(order)
            self._log(self.import_log, f"删除分组: {name}")
            self.refresh_internal_group_list()
        except Exception as e:
            QMessageBox.critical(self, "错误", f"{type(e).__name__}: {e}")

    def on_internal_file_double_clicked(self, item: QListWidgetItem) -> None:
        if item is None:
            return
        raw = item.data(Qt.UserRole)
        if not raw:
            return
        p = Path(str(raw))
        self.view_internal_file(p)

    def on_internal_file_context_menu(self, pos) -> None:
        item = self.internal_group_file_list.itemAt(pos)
        if item is None:
            return
        raw = item.data(Qt.UserRole)
        if not raw:
            return
        p = Path(str(raw))
        if not p.exists():
            return

        menu = QMenu(self)
        act_view = menu.addAction("查看")
        act_move = menu.addAction("移动到其他分组")
        act_delete = menu.addAction("删除")
        chosen = menu.exec_(self.internal_group_file_list.mapToGlobal(pos))
        if chosen == act_view:
            self.view_internal_file(p)
            return
        if chosen == act_move:
            self.move_files_to_other_group([p])
            return
        if chosen == act_delete:
            self.delete_internal_files([p])
            return

    def view_internal_file(self, path: Path) -> None:
        if not path.exists():
            QMessageBox.warning(self, "提示", "文件不存在")
            return
        dlg = ImagePreviewDialog(path, self)
        dlg.setAttribute(Qt.WA_DeleteOnClose, True)
        dlg.destroyed.connect(lambda _=None, d=dlg: self._preview_windows.remove(d) if d in self._preview_windows else None)
        self._preview_windows.append(dlg)
        dlg.show()

    def _selected_internal_file_paths(self) -> List[Path]:
        out: List[Path] = []
        for it in self.internal_group_file_list.selectedItems():
            raw = it.data(Qt.UserRole)
            if not raw:
                continue
            p = Path(str(raw))
            if p.exists() and p.is_file():
                out.append(p)
        return out

    def select_all_files(self) -> None:
        self.internal_group_file_list.selectAll()

    def clear_file_selection(self) -> None:
        self.internal_group_file_list.clearSelection()

    def choose_target_group(self, exclude: Optional[Path] = None) -> Optional[Path]:
        groups = [d for d in sorted(self.internal_root.iterdir()) if d.is_dir()]
        if exclude is not None:
            groups = [d for d in groups if d != exclude]
        if not groups:
            QMessageBox.warning(self, "提示", "当前没有分组")
            return None
        names = [d.name for d in groups]
        name, ok = QInputDialog.getItem(self, "选择分组", "选择分组", names, 0, False)
        if not ok or not name:
            return None
        return self.internal_root / name

    def _remap_single_md5_path(self, old_path: Path, new_path: Path) -> None:
        old_rel = str(old_path.relative_to(self.root)).replace("\\", "/")
        new_rel = str(new_path.relative_to(self.root)).replace("\\", "/")
        for k, v in list(self.md5_index.items()):
            vv = str(v).replace("\\", "/")
            if vv == old_rel:
                self.md5_index[k] = new_rel.replace("/", "\\")
                break

    def _remove_md5_entries_by_paths(self, paths: List[Path]) -> None:
        targets = {str(p.relative_to(self.root)).replace("\\", "/") for p in paths if p.exists() or p.parent.exists()}
        self.md5_index = {
            k: v
            for k, v in self.md5_index.items()
            if str(v).replace("\\", "/") not in targets
        }

    def move_files_to_other_group(self, files: List[Path]) -> None:
        if not files:
            QMessageBox.information(self, "提示", "当前没有选择文件")
            return
        src_group = files[0].parent
        target_group = self.choose_target_group(exclude=src_group)
        if target_group is None:
            return
        ensure_dir(target_group)

        moved = 0
        next_idx = next_image_index_in_folder(target_group, target_group.name)
        try:
            for p in files:
                if not p.exists():
                    continue
                ext = p.suffix.lower()
                dst = target_group / f"{target_group.name}{next_idx:03d}{ext}"
                next_idx += 1
                p.rename(dst)
                self._remap_single_md5_path(p, dst)
                moved += 1
            save_json(self.index_path, self.md5_index)
            self._log(self.import_log, f"移动文件: {moved} 个到 {target_group.name}")
            self.refresh_internal_group_list()
            self.select_internal_group(src_group if src_group.exists() else target_group)
        except Exception as e:
            QMessageBox.critical(self, "错误", f"{type(e).__name__}: {e}")

    def delete_internal_files(self, files: List[Path]) -> None:
        if not files:
            QMessageBox.information(self, "提示", "当前没有选择文件")
            return
        ans = QMessageBox.question(
            self,
            "删除文件",
            f"确定删除 {len(files)} 个文件吗？将会删除这些文件",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if ans != QMessageBox.Yes:
            return
        deleted_paths: List[Path] = []
        deleted = 0
        for p in files:
            try:
                if p.exists():
                    p.unlink()
                    deleted += 1
                    deleted_paths.append(p)
            except Exception:
                continue
        self._remove_md5_entries_by_paths(deleted_paths)
        save_json(self.index_path, self.md5_index)
        self._log(self.import_log, f"删除文件: {deleted} 个")
        keep_group = self.current_internal_group_dir if self.current_internal_group_dir else None
        self.refresh_internal_group_list()
        if keep_group and keep_group.exists():
            self.select_internal_group(keep_group)

    def batch_move_files(self) -> None:
        files = self._selected_internal_file_paths()
        self.move_files_to_other_group(files)

    def batch_delete_files(self) -> None:
        files = self._selected_internal_file_paths()
        self.delete_internal_files(files)

    def on_internal_group_changed(self, current: Optional[QListWidgetItem], previous: Optional[QListWidgetItem]) -> None:
        _ = previous
        self._stop_thumbnail_worker()
        self.internal_group_file_list.clear()
        self.current_internal_group_dir = None
        self._pending_group_dir = None
        if current is None:
            return
        raw = current.data(Qt.UserRole)
        if not raw:
            return
        group_dir = Path(str(raw))
        if not group_dir.exists() or not group_dir.is_dir():
            return
        self._pending_group_dir = group_dir
        # Debounce rapid clicks and only render the latest selected group.
        self._group_switch_timer.start(80)

    def _apply_pending_group_change(self) -> None:
        group_dir = self._pending_group_dir
        self._pending_group_dir = None
        if group_dir is None or not group_dir.exists() or not group_dir.is_dir():
            return
        self.current_internal_group_dir = group_dir
        self.populate_internal_group_files(group_dir)

    def on_internal_file_view_mode_changed(self, _idx: int) -> None:
        self.app_settings["internal_file_view_mode"] = self.internal_file_view_mode.currentText()
        save_json(self.settings_path, self.app_settings)
        if self.current_internal_group_dir and self.current_internal_group_dir.exists():
            self.populate_internal_group_files(self.current_internal_group_dir)

    def _stop_thumbnail_worker(self) -> None:
        worker = self._thumb_worker
        thread = self._thumb_worker_thread
        self._thumb_worker = None
        self._thumb_worker_thread = None

        if worker is not None:
            try:
                worker.thumbnail_ready.disconnect(self._on_thumbnail_ready)
            except Exception:
                pass
            try:
                worker.stop()
            except Exception:
                pass

        if thread is not None:
            try:
                thread.quit()
            except Exception:
                pass
            try:
                thread.wait(300)
            except Exception:
                pass

    def _start_thumbnail_worker(self, files: List[Path]) -> None:
        self._stop_thumbnail_worker()
        thread = QThread(self)
        worker = ThumbnailWorker(files, thumb_w=self.thumb_w, thumb_h=self.thumb_h)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.thumbnail_ready.connect(self._on_thumbnail_ready)
        worker.finished.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.start()
        self._thumb_worker_thread = thread
        self._thumb_worker = worker

    def _on_thumbnail_ready(self, row: int, icon: QIcon) -> None:
        # Ignore stale signals from workers that are no longer current.
        if self.sender() is not self._thumb_worker:
            return
        if self.internal_file_view_mode.currentText() != "缩略图视图":
            return
        if row < 0 or row >= self.internal_group_file_list.count():
            return
        item = self.internal_group_file_list.item(row)
        if item is None:
            return
        if not icon.isNull():
            item.setIcon(icon)

    def populate_internal_group_files(self, group_dir: Path) -> None:
        self._stop_thumbnail_worker()
        self.internal_group_file_list.setUpdatesEnabled(False)
        self.internal_group_file_list.clear()
        mode = self.internal_file_view_mode.currentText()
        files = [p for p in sorted(group_dir.iterdir()) if is_image_file(p)]

        if mode == "缩略图视图":
            self.internal_group_file_list.setViewMode(QListView.IconMode)
            self.internal_group_file_list.setIconSize(QSize(self.thumb_w, self.thumb_h))
            self.internal_group_file_list.setGridSize(QSize(self.thumb_grid_w, self.thumb_grid_h))
            self.internal_group_file_list.setResizeMode(QListView.Adjust)
            self.internal_group_file_list.setMovement(QListView.Static)
            self.internal_group_file_list.setWordWrap(True)
            self.internal_group_file_list.setSpacing(10)
            self.internal_group_file_list.setTextElideMode(Qt.ElideRight)
            self.internal_group_file_list.setWrapping(True)
            self.internal_group_file_list.setFlow(QListView.LeftToRight)
            self.internal_group_file_list.setUniformItemSizes(False)
            placeholder = self.style().standardIcon(QStyle.SP_FileIcon)

            for p in files:
                it = QListWidgetItem(placeholder, p.name)
                it.setTextAlignment(Qt.AlignHCenter)
                it.setData(Qt.UserRole, str(p))
                self.internal_group_file_list.addItem(it)
            self.internal_group_file_list.setUpdatesEnabled(True)
            self._start_thumbnail_worker(files)
            return

        self.internal_group_file_list.setViewMode(QListView.ListMode)
        self.internal_group_file_list.setIconSize(QSize(24, 24))
        self.internal_group_file_list.setGridSize(QSize())
        self.internal_group_file_list.setMovement(QListView.Static)
        self.internal_group_file_list.setWordWrap(False)
        self.internal_group_file_list.setSpacing(2)
        self.internal_group_file_list.setUniformItemSizes(True)
        file_icon = self.style().standardIcon(QStyle.SP_FileIcon)

        for p in files:
            st = p.stat()
            size_kb = st.st_size / 1024
            mtime = datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M")
            text = f"{p.name}    {size_kb:.1f} KB    {mtime}"
            it = QListWidgetItem(file_icon, text)
            it.setData(Qt.UserRole, str(p))
            self.internal_group_file_list.addItem(it)
        self.internal_group_file_list.setUpdatesEnabled(True)

    def closeEvent(self, event) -> None:
        self._group_switch_timer.stop()
        self._preview_debounce_timer.stop()
        self._stop_thumbnail_worker()
        super().closeEvent(event)

    def import_to_internal_library(self) -> None:
        feature_import_to_internal_library(
            self,
            list_images=list_images,
            md5_of_file=md5_of_file,
            ensure_dir=ensure_dir,
            next_image_index_in_folder=next_image_index_in_folder,
            compress_image_to_under_2mb=compress_image_to_under_2mb,
            is_image_file=is_image_file,
            save_json=save_json,
            max_import_image_bytes=MAX_IMPORT_IMAGE_BYTES,
        )

    def _build_group_tab(self) -> None:
        layout = QVBoxLayout(self.group_tab)

        cfg = QGridLayout()
        self.group_count = QSpinBox()
        self.group_count.setRange(1, 999)
        self.group_count.setValue(3)
        self.per_group = QSpinBox()
        self.per_group.setRange(16, 20)
        self.per_group.setValue(16)
        self.second_prefix = QLineEdit("第二层前缀")

        cfg.addWidget(QLabel("分组数量"), 0, 0)
        cfg.addWidget(self.group_count, 0, 1)
        cfg.addWidget(QLabel("每组图片数量"), 0, 2)
        cfg.addWidget(self.per_group, 0, 3)
        cfg.addWidget(QLabel("第二层前缀"), 0, 4)
        cfg.addWidget(self.second_prefix, 0, 5)
        layout.addLayout(cfg)

        btn_row = QHBoxLayout()
        self._normalize_row_spacing(btn_row)
        self.btn_pick_candidates = QPushButton("选择候选图片")
        self.btn_pick_candidates.clicked.connect(self.pick_candidates)
        self.btn_build_second = QPushButton("生成第二层文件夹")
        self.btn_build_second.clicked.connect(self.build_second_layer)
        btn_row.addWidget(self.btn_pick_candidates)
        btn_row.addWidget(self.btn_build_second)
        layout.addLayout(btn_row)

        self.candidate_list = QListWidget()
        layout.addWidget(self.candidate_list)

        self.group_log = QTextEdit()
        self.group_log.setReadOnly(True)
        layout.addWidget(self.group_log)

    def pick_candidates(self) -> None:
        feature_pick_candidates(self, list_images=list_images)

    def _resolve_candidate_item_path(self, item: QListWidgetItem) -> Optional[Path]:
        return feature_resolve_candidate_item_path(self, item)

    def build_second_layer(self) -> None:
        feature_build_second_layer(self, next_folder_index=next_folder_index, ensure_dir=ensure_dir)

    def _build_wm_tab(self) -> None:
        layout = QHBoxLayout(self.wm_tab)
        self.default_preview_image = self.root / "默认预览底图.jpg"

        left_col = QVBoxLayout()
        right_col = QVBoxLayout()
        left_col.setContentsMargins(0, 0, 0, 0)
        right_col.setContentsMargins(0, 0, 0, 0)
        left_col.setSpacing(8)
        right_col.setSpacing(8)

        top_box = QGroupBox("水印设置")
        top_box.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        top_box.setFixedHeight(96)
        top_layout = QFormLayout(top_box)
        wm_row = QHBoxLayout()
        self._normalize_row_spacing(wm_row)
        self.watermark_file_line = QLineEdit()
        pick_wm = QPushButton("选择PNG水印文件")
        pick_wm.clicked.connect(lambda: self._pick_file(self.watermark_file_line, "PNG Files (*.png)"))
        wm_row.addWidget(self.watermark_file_line)
        wm_row.addWidget(pick_wm)
        w_row1 = QWidget()
        w_row1.setLayout(wm_row)
        top_layout.addRow("PNG水印文件", w_row1)

        name_row = QHBoxLayout()
        self._normalize_row_spacing(name_row)
        self.watermark_name_line = QLineEdit()
        self.watermark_name_line.setPlaceholderText("请输入水印名称")
        add_wm = QPushButton("添加水印")
        add_wm.clicked.connect(self.add_watermark_file)
        name_row.addWidget(self.watermark_name_line)
        name_row.addWidget(add_wm)
        w_row2 = QWidget()
        w_row2.setLayout(name_row)
        top_layout.addRow("水印名称", w_row2)
        left_col.addWidget(top_box)

        setting_box = QGroupBox("水印设置")
        setting_box.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        setting_box.setFixedHeight(210)
        setting_layout = QGridLayout(setting_box)
        setting_layout.setHorizontalSpacing(8)
        setting_layout.setVerticalSpacing(8)

        self.wm_selector = QComboBox()
        self.wm_selector.setIconSize(QSize(44, 44))
        wm_view = QListView()
        wm_view.setIconSize(QSize(44, 44))
        wm_view.setSpacing(4)
        self.wm_selector.setView(wm_view)
        self.wm_selector.setItemDelegate(WatermarkComboDelegate(self.wm_selector))
        self.wm_selector.currentIndexChanged.connect(self._on_wm_selector_changed)

        self.wm_rotate_slider = self._make_slider(-180, 180, 0, self.update_preview)
        self.wm_opacity_slider = self._make_slider(1, 100, 100, self.update_preview)
        self.show_border_checkbox = QCheckBox("显示边框")
        self.show_border_checkbox.setChecked(False)
        self.show_border_checkbox.toggled.connect(self.update_preview)
        row_h = self.wm_rotate_slider.sizeHint().height()
        self.wm_selector.setFixedHeight(row_h)
        self.show_border_checkbox.setFixedHeight(row_h)

        wm_pick_row = QHBoxLayout()
        self._normalize_row_spacing(wm_pick_row)
        wm_pick_row.addWidget(self.wm_selector, 1)
        wm_pick_row.addWidget(self.show_border_checkbox, 0, Qt.AlignVCenter)
        wm_pick_wrap = QWidget()
        wm_pick_wrap.setLayout(wm_pick_row)
        wm_pick_wrap.setFixedHeight(row_h)

        setting_layout.addWidget(QLabel("选择水印"), 0, 0)
        setting_layout.addWidget(wm_pick_wrap, 0, 1, 1, 3)
        setting_layout.addWidget(QLabel("旋转(度)"), 1, 0)
        setting_layout.addWidget(self.wm_rotate_slider, 1, 1, 1, 3)
        setting_layout.addWidget(QLabel("透明度(%)"), 2, 0)
        setting_layout.addWidget(self.wm_opacity_slider, 2, 1, 1, 3)

        btn_apply = QPushButton("应用")
        btn_apply.clicked.connect(self.apply_wm_settings)
        btn_reset = QPushButton("重置")
        btn_reset.clicked.connect(self.reset_wm_settings)
        btn_delete = QPushButton("删除")
        btn_delete.clicked.connect(self.delete_selected_watermark)
        setting_layout.addWidget(btn_apply, 3, 1)
        setting_layout.addWidget(btn_reset, 3, 2)
        setting_layout.addWidget(btn_delete, 3, 3)
        left_col.addWidget(setting_box)

        cfg_box = QGroupBox("配置设置")
        cfg_box.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        cfg_box.setFixedHeight(205)
        cfg_layout = QGridLayout(cfg_box)

        self.cfg_name = QLineEdit("")
        self.final_x_slider = self._make_slider(0, 100, 50, self.update_preview)
        self.final_y_slider = self._make_slider(0, 100, 50, self.update_preview)
        self.final_scale_slider = self._make_slider(5, 100, 25, self.update_preview)
        self.final_rotate_slider = self._make_slider(-180, 180, 0, self.update_preview)
        self.final_opacity_slider = self._make_slider(1, 100, 100, self.update_preview)
        self._preview_sliders = [
            self.wm_rotate_slider,
            self.wm_opacity_slider,
            self.final_x_slider,
            self.final_y_slider,
            self.final_scale_slider,
            self.final_rotate_slider,
            self.final_opacity_slider,
        ]
        for s in self._preview_sliders:
            s.editingFinished.connect(self.request_preview_update)

        cfg_layout.addWidget(QLabel("配置名称"), 0, 0)
        cfg_layout.addWidget(self.cfg_name, 0, 1, 1, 3)
        cfg_layout.addWidget(QLabel("X(%)"), 1, 0)
        cfg_layout.addWidget(self.final_x_slider, 1, 1, 1, 3)
        cfg_layout.addWidget(QLabel("Y(%)"), 2, 0)
        cfg_layout.addWidget(self.final_y_slider, 2, 1, 1, 3)
        cfg_layout.addWidget(QLabel("缩放(%)"), 3, 0)
        cfg_layout.addWidget(self.final_scale_slider, 3, 1, 1, 3)
        cfg_layout.addWidget(QLabel("旋转(度)"), 4, 0)
        cfg_layout.addWidget(self.final_rotate_slider, 4, 1, 1, 3)
        cfg_layout.addWidget(QLabel("透明度(%)"), 5, 0)
        cfg_layout.addWidget(self.final_opacity_slider, 5, 1, 1, 3)

        left_col.addWidget(cfg_box)

        sample_row = QHBoxLayout()
        self._normalize_row_spacing(sample_row)
        self.sample_image_line = QLineEdit(str(self.default_preview_image))
        pick_sample = QPushButton("选择预览图片")
        pick_sample.clicked.connect(lambda: self._pick_file(self.sample_image_line, "Image Files (*.png *.jpg *.jpeg *.webp *.bmp)"))
        sample_row.addWidget(self.sample_image_line)
        sample_row.addWidget(pick_sample)
        sample_w = QWidget()
        sample_w.setLayout(sample_row)
        sample_w.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        sample_w.setFixedHeight(34)
        left_col.addWidget(sample_w)

        btn_row = QHBoxLayout()
        self._normalize_row_spacing(btn_row)
        self.config_selector = QComboBox()
        self.config_selector.currentIndexChanged.connect(self.load_selected_config)
        save_btn = QPushButton("保存配置")
        save_btn.clicked.connect(self.save_current_config)
        btn_row.addWidget(QLabel("配置列表"))
        btn_row.addWidget(self.config_selector)
        btn_row.addWidget(save_btn)
        btn_w = QWidget()
        btn_w.setLayout(btn_row)
        btn_w.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        btn_w.setFixedHeight(34)
        left_col.addWidget(btn_w)

        self.preview_label = OverlayPreviewLabel("预览效果")
        self.preview_label.setAlignment(Qt.AlignCenter)
        self.preview_label.setMinimumHeight(560)
        self.preview_label.setStyleSheet("border: 1px solid #999;")
        wm_show_box = QGroupBox("水印预览")
        wm_show_layout = QVBoxLayout(wm_show_box)
        self.current_wm_preview = OverlayPreviewLabel("当前水印预览")
        self.current_wm_preview.setAlignment(Qt.AlignCenter)
        self.current_wm_preview.setMinimumHeight(120)
        self.current_wm_preview.setMaximumHeight(120)
        self.current_wm_preview.setStyleSheet("border: 1px solid #999;")
        self.preview_label.overlayDragged.connect(self._on_effect_overlay_dragged)
        self.current_wm_name = QLabel("当前水印名称")
        self.current_wm_name.setAlignment(Qt.AlignCenter)
        self.current_wm_name.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        self.current_wm_name.setFixedHeight(22)
        wm_show_layout.addWidget(self.current_wm_preview)
        wm_show_layout.addWidget(self.current_wm_name)
        wm_show_box.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        wm_show_box.setFixedHeight(176)
        right_col.addWidget(wm_show_box, 0)

        effect_box = QGroupBox("效果预览")
        effect_layout = QVBoxLayout(effect_box)
        effect_layout.setContentsMargins(6, 12, 6, 6)
        effect_layout.addWidget(self.preview_label, 1)
        right_col.addWidget(effect_box, 1)

        self.wm_log = QTextEdit()
        self.wm_log.setReadOnly(True)
        self.wm_log.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Expanding)
        left_col.addWidget(self.wm_log, 1)

        layout.addLayout(left_col, 2)
        layout.addLayout(right_col, 3)
        self.update_preview()

    def _make_slider(self, min_v: int, max_v: int, default: int, callback) -> QSpinBox:
        s = QSpinBox()
        s.setRange(min_v, max_v)
        s.setValue(default)
        s.setSingleStep(1)
        s.setAccelerated(True)
        s.setButtonSymbols(QAbstractSpinBox.UpDownArrows)
        s.setAlignment(Qt.AlignCenter)
        if callback == self.update_preview:
            # Real-time update with coalescing: every change requests a render tick.
            s.valueChanged.connect(self.request_preview_update)
        else:
            s.valueChanged.connect(callback)
        return s

    def request_preview_update(self) -> None:
        self._preview_dirty = True
        if self._preview_rendering:
            return
        self._preview_debounce_timer.start(self._preview_interval_for_state())

    def _is_preview_slider_active(self) -> bool:
        # SpinBox has no drag state; keep preview in normal realtime mode.
        return False

    def _preview_interval_for_state(self) -> int:
        return self._preview_interval_drag_ms if self._is_preview_slider_active() else self._preview_interval_normal_ms

    def add_watermark_file(self) -> None:
        feature_add_watermark_file(self)

    def apply_wm_settings(self) -> None:
        feature_apply_wm_settings(self)

    def reset_wm_settings(self) -> None:
        feature_reset_wm_settings(self)

    def _reset_wm_setting_sliders(self) -> None:
        self.wm_rotate_slider.setValue(0)
        self.wm_opacity_slider.setValue(100)

    def delete_selected_watermark(self) -> None:
        feature_delete_selected_watermark(self)

    def _selected_wm_filename(self) -> str:
        data = self.wm_selector.currentData()
        if isinstance(data, str) and data:
            return data
        return self.wm_selector.currentText().strip()

    def _make_wm_selector_icon(self, p: Path, size: int = 44) -> QIcon:
        pix = QPixmap(str(p))
        if pix.isNull():
            return QIcon()
        pix = pix.scaled(size, size, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        return QIcon(pix)

    def _render_checkerboard_preview(self, src_pix: QPixmap, target_w: int, target_h: int) -> QPixmap:
        # Render PNG alpha on checkerboard background for transparent-channel visibility.
        w = max(1, target_w)
        h = max(1, target_h)
        canvas = QPixmap(w, h)
        canvas.fill(QColor(0, 0, 0, 0))
        p = QPainter(canvas)
        c1 = QColor(238, 238, 238)
        c2 = QColor(212, 212, 212)
        block = 10
        for y in range(0, h, block):
            for x in range(0, w, block):
                p.fillRect(x, y, block, block, c1 if ((x // block + y // block) % 2 == 0) else c2)
        if not src_pix.isNull():
            draw = src_pix.scaled(w - 8, h - 8, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            dx = (w - draw.width()) // 2
            dy = (h - draw.height()) // 2
            p.drawPixmap(dx, dy, draw)
        p.end()
        return canvas

    def _get_cached_sample_preview(self, sample_path: Path, tw: int, th: int) -> Image.Image:
        st = sample_path.stat()
        key = (str(sample_path), st.st_mtime, tw, th)
        if self._sample_preview_cache_key == key and self._sample_preview_cache_img is not None:
            return self._sample_preview_cache_img.copy()

        with Image.open(sample_path) as img:
            src = img.convert("RGB")
            fitted = ImageOps.contain(src, (tw, th), method=Image.Resampling.LANCZOS)
            canvas = Image.new("RGB", (tw, th), (32, 32, 32))
            x = (tw - fitted.width) // 2
            y = (th - fitted.height) // 2
            canvas.paste(fitted, (x, y))
        self._sample_preview_cache_key = key
        self._sample_preview_cache_img = canvas
        return canvas.copy()

    def _get_cached_wm_raw(self, wm_path: Path) -> Image.Image:
        st = wm_path.stat()
        key = (str(wm_path), st.st_mtime)
        if self._wm_raw_cache_key == key and self._wm_raw_cache_img is not None:
            return self._wm_raw_cache_img.copy()
        with Image.open(wm_path) as wm:
            raw = wm.convert("RGBA")
        self._wm_raw_cache_key = key
        self._wm_raw_cache_img = raw
        return raw.copy()

    def _apply_watermark_image_settings(self, wm_img: Image.Image, fast_mode: bool = False) -> Image.Image:
        # Watermark settings: process the watermark image itself.
        mark = wm_img.convert("RGBA")
        rotate_resample = Image.Resampling.BILINEAR if fast_mode else Image.Resampling.BICUBIC
        mark = mark.rotate(self.wm_rotate_slider.value(), expand=True, resample=rotate_resample)

        op = self.wm_opacity_slider.value()
        if op < 100:
            alpha = mark.getchannel("A").point(lambda x: int(x * op / 100))
            mark.putalpha(alpha)
        return mark

    def _compose_effect_preview(
        self, base_img: Image.Image, wm_layer: Image.Image, fast_mode: bool = False
    ) -> Tuple[Image.Image, Tuple[float, float, float, float]]:
        # Preview-only composition: simulate two layers (base + watermark), no file write.
        base = base_img.convert("RGBA")
        mark = wm_layer.convert("RGBA")
        resize_resample = Image.Resampling.BILINEAR if fast_mode else Image.Resampling.LANCZOS
        rotate_resample = Image.Resampling.BILINEAR if fast_mode else Image.Resampling.BICUBIC

        target_w = max(8, int(base.width * self.final_scale_slider.value() / 100))
        ratio = target_w / max(1, mark.width)
        target_h = max(8, int(mark.height * ratio))
        mark = mark.resize((target_w, target_h), resize_resample)
        mark = mark.rotate(self.final_rotate_slider.value(), expand=True, resample=rotate_resample)

        op = self.final_opacity_slider.value()
        if op < 100:
            alpha = mark.getchannel("A").point(lambda x: int(x * op / 100))
            mark.putalpha(alpha)

        cx = int(base.width * self.final_x_slider.value() / 100)
        cy = int(base.height * self.final_y_slider.value() / 100)
        x = cx - mark.width // 2
        y = cy - mark.height // 2

        wm_overlay = Image.new("RGBA", base.size, (0, 0, 0, 0))
        wm_overlay.paste(mark, (x, y), mark)
        out = Image.alpha_composite(base, wm_overlay)
        bbox = mark.getchannel("A").getbbox()
        if bbox:
            l, t, r, b = bbox
            ax = x + l
            ay = y + t
            aw = max(1, r - l)
            ah = max(1, b - t)
        else:
            ax, ay, aw, ah = x, y, mark.width, mark.height
        bx = ax / float(max(1, base.width))
        by = ay / float(max(1, base.height))
        bw = aw / float(max(1, base.width))
        bh = ah / float(max(1, base.height))
        return out, self._clamp_norm_rect((bx, by, bw, bh))

    def _get_cached_simulated_wm(self, wm_path: Path, fast_mode: bool) -> Image.Image:
        st = wm_path.stat()
        key = (
            str(wm_path),
            st.st_mtime,
            self.wm_rotate_slider.value(),
            self.wm_opacity_slider.value(),
            fast_mode,
        )
        if self._wm_sim_cache_key == key and self._wm_sim_cache_img is not None:
            return self._wm_sim_cache_img.copy()
        wm_raw = self._get_cached_wm_raw(wm_path)
        wm_processed = self._apply_watermark_image_settings(wm_raw, fast_mode=fast_mode)
        self._wm_sim_cache_key = key
        self._wm_sim_cache_img = wm_processed
        return wm_processed.copy()

    def _get_cached_wm_preview_pix(
        self,
        wm_processed: Image.Image,
        target_w: int,
        target_h: int,
        cache_tag: Tuple[object, ...],
    ) -> Tuple[QPixmap, Tuple[float, float, float, float]]:
        key = (cache_tag, target_w, target_h)
        if (
            self._wm_preview_pix_cache_key == key
            and self._wm_preview_pix_cache_img is not None
            and self._wm_preview_pix_cache_bbox_norm is not None
        ):
            return QPixmap(self._wm_preview_pix_cache_img), self._wm_preview_pix_cache_bbox_norm

        w = max(1, target_w)
        h = max(1, target_h)
        padding = 1
        wm_thumb_src = pil_to_qpixmap(wm_processed)
        if wm_thumb_src.isNull():
            return QPixmap(), (0.0, 0.0, 1.0, 1.0)

        canvas = QPixmap(w, h)
        canvas.fill(QColor(0, 0, 0, 0))
        p = QPainter(canvas)
        c1 = QColor(238, 238, 238)
        c2 = QColor(212, 212, 212)
        block = 10
        for y in range(0, h, block):
            for x in range(0, w, block):
                p.fillRect(x, y, block, block, c1 if ((x // block + y // block) % 2 == 0) else c2)

        avail_w = max(1, w - 2 * padding)
        avail_h = max(1, h - 2 * padding)
        draw = wm_thumb_src.scaled(avail_w, avail_h, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        dx = (w - draw.width()) // 2
        dy = (h - draw.height()) // 2
        p.drawPixmap(dx, dy, draw)
        p.end()

        rgba = wm_processed.convert("RGBA")
        bbox = rgba.getchannel("A").getbbox()
        if bbox and wm_processed.width > 0 and wm_processed.height > 0:
            l, t, r, b = bbox
            sx = draw.width() / float(wm_processed.width)
            sy = draw.height() / float(wm_processed.height)
            ax = dx + l * sx
            ay = dy + t * sy
            aw = max(1.0, (r - l) * sx)
            ah = max(1.0, (b - t) * sy)
            bbox_norm = self._clamp_norm_rect((ax / w, ay / h, aw / w, ah / h))
        else:
            bbox_norm = self._clamp_norm_rect(
                (dx / float(w), dy / float(h), draw.width() / float(w), draw.height() / float(h))
            )

        self._wm_preview_pix_cache_key = key
        self._wm_preview_pix_cache_img = QPixmap(canvas)
        self._wm_preview_pix_cache_bbox_norm = bbox_norm
        return canvas, bbox_norm

    @staticmethod
    def _clamp_norm_rect(rect: Tuple[float, float, float, float]) -> Tuple[float, float, float, float]:
        x, y, w, h = rect
        x = max(0.0, min(1.0, x))
        y = max(0.0, min(1.0, y))
        w = max(0.01, min(1.0 - x, w))
        h = max(0.01, min(1.0 - y, h))
        return (x, y, w, h)

    def _alpha_bbox_norm(self, img: Image.Image) -> Tuple[float, float, float, float]:
        rgba = img.convert("RGBA")
        bbox = rgba.getchannel("A").getbbox()
        if not bbox:
            return (0.0, 0.0, 1.0, 1.0)
        l, t, r, b = bbox
        w = max(1, rgba.width)
        h = max(1, rgba.height)
        return self._clamp_norm_rect((l / w, t / h, (r - l) / w, (b - t) / h))

    def _on_wm_border_changed(self, rect_norm) -> None:
        _ = rect_norm
        return

    def _on_fx_border_changed(self, rect_norm) -> None:
        _ = rect_norm
        return

    def _on_manual_border_toggled(self, _checked: bool) -> None:
        self.update_preview()

    def _on_wm_selector_changed(self, _idx: int) -> None:
        # New watermark should start with current frame aligned to actual frame.
        self._wm_current_border_norm = None
        self._fx_current_border_norm = None
        self.update_preview()

    def _on_effect_overlay_dragged(self, dx_norm: float, dy_norm: float) -> None:
        x = self.final_x_slider.value() + int(round(dx_norm * 100))
        y = self.final_y_slider.value() + int(round(dy_norm * 100))
        self.final_x_slider.setValue(max(0, min(100, x)))
        self.final_y_slider.setValue(max(0, min(100, y)))

    def refresh_watermark_lists(self) -> None:
        feature_refresh_watermark_lists(self)

    def current_cfg_obj(self) -> Optional[WatermarkConfig]:
        return feature_current_cfg_obj(self, WatermarkConfig)

    def save_current_config(self) -> None:
        feature_save_current_config(self, save_json=save_json, watermark_config_cls=WatermarkConfig)

    def load_selected_config(self) -> None:
        feature_load_selected_config(self, load_json=load_json)

    def update_preview(self) -> None:
        if self._preview_rendering:
            self._preview_dirty = True
            return
        self._preview_debounce_timer.stop()
        self._preview_rendering = True
        self._preview_dirty = False
        fast_mode = self._is_preview_slider_active()
        sample_path = Path(self.sample_image_line.text().strip()) if self.sample_image_line.text().strip() else self.default_preview_image
        if not sample_path.exists() and self.default_preview_image.exists():
            sample_path = self.default_preview_image
            self.sample_image_line.setText(str(sample_path))
        if not sample_path.exists():
            internal_imgs = list_images(self.second_root)
            sample_path = internal_imgs[0] if internal_imgs else None
            if sample_path is None:
                self.preview_label.setText("鏈壘鍒伴粯璁ら瑙堝簳鍥?jpg锛岃鎵嬪姩閫夋嫨棰勮搴曞浘")
                self._preview_rendering = False
                return

        try:
            tw = max(1, self.preview_label.width() - 8)
            th = max(1, self.preview_label.height() - 8)
            base_preview = self._get_cached_sample_preview(sample_path, tw, th)
            effect_preview = base_preview.convert("RGBA")
            show_frames = self.show_border_checkbox.isChecked()
            self._wm_actual_border_norm = None
            self._fx_actual_border_norm = None

            # Show base image first even if watermark is not selected yet.
            wm_name = self._selected_wm_filename()
            wm_path = self.watermark_root / wm_name if wm_name else None
            if wm_path is not None and wm_path.exists():
                wm_raw_actual = self._get_cached_wm_raw(wm_path)
                wm_processed_current = self._get_cached_simulated_wm(wm_path, fast_mode=fast_mode)
                wm_stat = wm_path.stat()
                wm_thumb, wm_current_bbox_norm = self._get_cached_wm_preview_pix(
                    wm_processed_current,
                    max(1, self.current_wm_preview.width() - 2),
                    max(1, self.current_wm_preview.height() - 2),
                    cache_tag=("wm_preview_current", str(wm_path), wm_stat.st_mtime, self.wm_rotate_slider.value(), self.wm_opacity_slider.value()),
                )
                _, wm_actual_bbox_norm = self._get_cached_wm_preview_pix(
                    wm_raw_actual,
                    max(1, self.current_wm_preview.width() - 2),
                    max(1, self.current_wm_preview.height() - 2),
                    cache_tag=("wm_preview_actual", str(wm_path), wm_stat.st_mtime),
                )
                self._wm_actual_border_norm = wm_actual_bbox_norm
                self._wm_current_border_norm = wm_current_bbox_norm
                if not wm_thumb.isNull():
                    self.current_wm_preview.setPixmap(wm_thumb)
                    self.current_wm_preview.setText("")
                else:
                    self.current_wm_preview.setPixmap(QPixmap())
                    self.current_wm_preview.setText("姘村嵃棰勮澶辫触")
                self.current_wm_name.setText(wm_path.stem)
                # Two-layer simulation for effect preview; no persistent image data change.
                effect_preview, self._fx_current_border_norm = self._compose_effect_preview(
                    base_preview,
                    wm_processed_current,
                    fast_mode=fast_mode,
                )
                _, self._fx_actual_border_norm = self._compose_effect_preview(
                    base_preview,
                    wm_raw_actual,
                    fast_mode=fast_mode,
                )
            else:
                empty = self._render_checkerboard_preview(QPixmap(), max(1, self.current_wm_preview.width() - 2), max(1, self.current_wm_preview.height() - 2))
                self.current_wm_preview.setPixmap(empty)
                self.current_wm_preview.setText("当前水印预览")
                self.current_wm_name.setText("当前水印名称")
                self._wm_current_border_norm = None
                self._fx_current_border_norm = None

            pix = pil_to_qpixmap(effect_preview)
            # Always fit the whole image into preview area without crop.
            pix = pix.scaled(
                max(1, self.preview_label.width() - 8),
                max(1, self.preview_label.height() - 8),
                Qt.KeepAspectRatio,
                Qt.FastTransformation if fast_mode else Qt.SmoothTransformation,
            )
            self.preview_label.setPixmap(pix)
            self.current_wm_preview.set_overlay_rects(
                self._wm_actual_border_norm,
                self._wm_current_border_norm,
                show_frames,
                False,
            )
            self.preview_label.set_overlay_rects(
                self._fx_actual_border_norm,
                self._fx_current_border_norm,
                show_frames,
                wm_path is not None and wm_path.exists(),
            )
        except Exception as e:
            self.preview_label.setText(f"预览错误: {type(e).__name__}: {e}")
        finally:
            self._preview_rendering = False
            if self._preview_dirty:
                self._preview_debounce_timer.start(self._preview_interval_for_state())

    def _build_export_tab(self) -> None:
        layout = QVBoxLayout(self.export_tab)

        folder_box = QGroupBox("导出设置")
        f = QFormLayout(folder_box)
        self.second_line = QLineEdit(str(self.second_root))
        btn1 = QPushButton("选择第二层文件夹")
        btn1.clicked.connect(lambda: self._pick_dir(self.second_line))
        row1 = QHBoxLayout()
        self._normalize_row_spacing(row1)
        row1.addWidget(self.second_line)
        row1.addWidget(btn1)
        w1 = QWidget()
        w1.setLayout(row1)
        f.addRow("第二层文件夹", w1)

        self.export_line = QLineEdit(str(self.export_root))
        btn2 = QPushButton("选择导出文件夹")
        btn2.clicked.connect(lambda: self._pick_dir(self.export_line))
        row2 = QHBoxLayout()
        self._normalize_row_spacing(row2)
        row2.addWidget(self.export_line)
        row2.addWidget(btn2)
        w2 = QWidget()
        w2.setLayout(row2)
        f.addRow("导出文件夹", w2)

        self.export_config_selector = QComboBox()
        f.addRow("配置列表", self.export_config_selector)
        layout.addWidget(folder_box)

        btn_row = QHBoxLayout()
        self._normalize_row_spacing(btn_row)
        refresh_btn = QPushButton("刷新导出文件夹列表")
        refresh_btn.clicked.connect(self.refresh_export_folder_list)
        run_btn = QPushButton("执行导出")
        run_btn.clicked.connect(self.batch_export)
        btn_row.addWidget(refresh_btn)
        btn_row.addWidget(run_btn)
        layout.addLayout(btn_row)

        self.second_folder_list = QListWidget()
        layout.addWidget(self.second_folder_list)

        self.export_log = QTextEdit()
        self.export_log.setReadOnly(True)
        layout.addWidget(self.export_log)

        self.refresh_export_folder_list()
        self.refresh_export_config_list()

    def refresh_export_config_list(self) -> None:
        feature_refresh_export_config_list(self)

    def refresh_export_folder_list(self) -> None:
        feature_refresh_export_folder_list(self, is_image_file=is_image_file)

    def batch_export(self) -> None:
        feature_batch_export(
            self,
            load_json=load_json,
            apply_watermark=apply_watermark,
            ensure_dir=ensure_dir,
            is_image_file=is_image_file,
            watermark_config_cls=WatermarkConfig,
        )


def main() -> None:
    app = QApplication(sys.argv)
    w = MainWindow()
    w.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()

