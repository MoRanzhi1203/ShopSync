# -*- coding: utf-8 -*-
import os
import random
import shutil
import subprocess
import sys
import time
import ctypes
import base64
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from PIL import Image, ImageOps
from PyQt5.QtCore import QEvent, QObject, QRect, QRectF, QSize, Qt, QThread, QTimer, pyqtSignal
from PyQt5.QtGui import QColor, QIcon, QImage, QPainter, QPen, QPixmap
from PyQt5.QtWidgets import (
    QAbstractSpinBox,
    QAbstractItemView,
    QApplication,
    QListView,
    QTreeView,
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
    QStyleOptionButton,
    QSizePolicy,
)

from features.batch_export import batch_export as feature_batch_export
from features.batch_export import refresh_export_config_list as feature_refresh_export_config_list
from features.batch_export import refresh_export_folder_list as feature_refresh_export_folder_list
from features.import_dedup import run_import_to_internal_library as feature_import_to_internal_library
from features.product_grouping import build_second_layer as feature_build_second_layer
from features.product_grouping import resolve_candidate_item_path as feature_resolve_candidate_item_path
from features.watermark_config import add_watermark_file as feature_add_watermark_file
from features.watermark_config import apply_wm_settings as feature_apply_wm_settings
from features.watermark_config import current_cfg_obj as feature_current_cfg_obj
from features.watermark_config import delete_selected_watermark as feature_delete_selected_watermark
from features.watermark_config import load_selected_config as feature_load_selected_config
from features.watermark_config import refresh_watermark_lists as feature_refresh_watermark_lists
from features.watermark_config import reset_wm_settings as feature_reset_wm_settings
from features.watermark_config import save_current_config as feature_save_current_config
import resources_rc  # noqa: F401  # Registers Qt resources at import time.

from features.utils import (
    IMAGE_EXTS,
    MAX_IMPORT_IMAGE_BYTES,
    app_data_base_dir,
    app_resource_base_dir,
    apply_watermark,
    compress_image_to_under_size,
    ensure_dir,
    extract_qrc_file_to_path,
    is_image_file,
    is_valid_image,
    list_images,
    load_json,
    md5_of_file,
    next_folder_index,
    next_image_index_in_folder,
    pil_to_qpixmap,
    place_mark,
    resolve_data_path,
    resolve_resource_path,
    rotate_keep_scale,
    save_json,
    trim_alpha_border,
)

compress_image_to_under_2mb = compress_image_to_under_size


@dataclass
class WatermarkConfig:
    name: str
    watermark_file: str
    x_pct: int = 50
    y_pct: int = 50
    scale_pct: int = 25
    rotate_deg: int = 0
    opacity_pct: int = 100
    constrain_inside: bool = True


class ThumbnailWorker(QObject):
    thumbnail_ready = pyqtSignal(int, QIcon)
    finished = pyqtSignal()

    def __init__(self, tasks: List[Tuple[int, Path]], thumb_w: int = 176, thumb_h: int = 102) -> None:
        super().__init__()
        self.tasks = tasks
        self.thumb_w = thumb_w
        self.thumb_h = thumb_h
        self._stop = False

    def stop(self) -> None:
        self._stop = True

    def run(self) -> None:
        try:
            for row, path in self.tasks:
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

        icon_size = 52
        top_margin = 2
        icon_x = rect.x() + (rect.width() - icon_size) // 2
        icon_y = rect.y() + top_margin
        if isinstance(icon, QIcon) and not icon.isNull():
            icon.paint(painter, QRect(icon_x, icon_y, icon_size, icon_size), Qt.AlignCenter)

        painter.setPen(text_color)
        bottom_padding = 3
        text_rect = QRect(
            rect.x() + 4,
            rect.y() + top_margin + icon_size + 2,
            rect.width() - 8,
            max(1, rect.height() - icon_size - top_margin - 2 - bottom_padding),
        )
        painter.drawText(text_rect, Qt.AlignHCenter | Qt.AlignBottom, text)
        painter.restore()

    def sizeHint(self, option, index):
        _ = option, index
        return QSize(96, 76)


class InternalGroupStatusDelegate(QStyledItemDelegate):
    def paint(self, painter, option, index):
        painter.save()
        rect = option.rect
        text = str(index.data(Qt.DisplayRole) or "")
        if option.state & QStyle.State_Selected:
            painter.fillRect(rect, option.palette.highlight())
            base_color = option.palette.highlightedText().color()
        else:
            painter.fillRect(rect, option.palette.base())
            base_color = option.palette.text().color()

        suffix = ""
        prefix = text
        marker = "  ["
        if marker in text and text.endswith("]"):
            prefix, suffix = text.rsplit(marker, 1)
            suffix = marker + suffix

        draw_rect = rect.adjusted(6, 0, -6, 0)
        check_state = index.data(Qt.CheckStateRole)
        has_checkbox = check_state is not None
        if has_checkbox:
            indicator = QStyleOptionButton()
            indicator.state = QStyle.State_Enabled
            indicator.state |= QStyle.State_On if int(check_state) == int(Qt.Checked) else QStyle.State_Off
            indicator_rect = QApplication.style().subElementRect(QStyle.SE_CheckBoxIndicator, indicator, None)
            indicator_rect.moveLeft(draw_rect.left())
            indicator_rect.moveTop(draw_rect.y() + (draw_rect.height() - indicator_rect.height()) // 2)
            indicator.rect = indicator_rect
            QApplication.style().drawPrimitive(QStyle.PE_IndicatorCheckBox, indicator, painter)
            draw_rect.setLeft(indicator_rect.right() + 6)

        fm = painter.fontMetrics()
        suffix_width = fm.horizontalAdvance(suffix) if suffix else 0
        prefix_rect = draw_rect.adjusted(0, 0, -suffix_width, 0) if suffix else draw_rect

        painter.setPen(base_color)
        painter.drawText(prefix_rect, Qt.AlignVCenter | Qt.AlignLeft, prefix)
        if suffix:
            suffix_rect = draw_rect.adjusted(max(0, draw_rect.width() - suffix_width), 0, 0, 0)

            font = painter.font()
            font.setBold(False)

            if option.state & QStyle.State_Selected:
                suffix_color = QColor(255, 255, 255)
                if "[水印]" in suffix:
                    font.setBold(True)
            else:
                suffix_color = QColor(210, 54, 57) if "[水印]" in suffix else QColor(90, 90, 90)

            painter.setFont(font)
            painter.setPen(suffix_color)
            painter.drawText(suffix_rect, Qt.AlignVCenter | Qt.AlignLeft, suffix)

        painter.restore()

    def sizeHint(self, option, index):
        return super().sizeHint(option, index)

class OverlayPreviewLabel(QLabel):
    borderChanged = pyqtSignal(object)
    overlayDragged = pyqtSignal(float, float)
    overlayDragStateChanged = pyqtSignal(bool)

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
                    self.overlayDragStateChanged.emit(True)
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
        if self._drag_move:
            self.overlayDragStateChanged.emit(False)
        self._drag_move = False
        self._drag_edge = None
        super().mouseReleaseEvent(event)


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("商品分组水印工具")
        self.resize(1200, 860)
        icon_path = resolve_resource_path("shopsync_data", "assets", "app.ico")
        if not icon_path.exists():
            icon_path = resolve_data_path("shopsync_data", "assets", "app.ico")
        if icon_path.exists():
            self.setWindowIcon(QIcon(str(icon_path)))

        self.resource_root = app_resource_base_dir()
        self.data_root = app_data_base_dir()

        # Group runtime data/output folders under one parent directory for portable deployment.
        self.workspace_root = self.data_root / "shopsync_data"
        # Runtime writable root for all app data.
        self.root = self.workspace_root
        self._migrate_legacy_data_layout()
        self.internal_root = self.workspace_root / "internal_library"
        self.second_root = self.workspace_root / "second_layer"
        self.export_root = self.workspace_root / "export_images"
        self.watermark_root = self.workspace_root / "watermark_library" / "images"
        self.config_root = self.workspace_root / "watermark_library" / "configs"
        self.wm_settings_path = self.workspace_root / "watermark_library" / "wm_settings.json"
        self.index_path = self.workspace_root / "internal_library" / "md5_index.json"
        self.settings_path = self.workspace_root / "app_settings.json"

        for p in [self.internal_root, self.second_root, self.export_root, self.watermark_root, self.config_root]:
            ensure_dir(p)

        self.md5_index: Dict[str, str] = load_json(self.index_path, {})
        self._normalize_md5_index_paths()
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
        self._thumb_files: List[Path] = []
        self._thumb_loaded_rows: set[int] = set()
        self._thumb_schedule_timer = QTimer(self)
        self._thumb_schedule_timer.setSingleShot(True)
        self._thumb_schedule_timer.timeout.connect(self._schedule_thumbnail_loading)
        self._group_switch_timer = QTimer(self)
        self._group_switch_timer.setSingleShot(True)
        self._group_switch_timer.timeout.connect(self._apply_pending_group_change)
        self._preview_debounce_timer = QTimer(self)
        self._preview_debounce_timer.setSingleShot(True)
        self._preview_debounce_timer.timeout.connect(self.update_preview)
        self._preview_interval_normal_ms = 16
        self._preview_interval_drag_ms = 12
        self._preview_rendering = False
        self._preview_dirty = False
        self._overlay_dragging = False
        self._overlay_drag_accum_x = 0.0
        self._overlay_drag_accum_y = 0.0
        self._overlay_drag_idle_timer = QTimer(self)
        self._overlay_drag_idle_timer.setSingleShot(True)
        self._overlay_drag_idle_timer.timeout.connect(lambda: setattr(self, "_overlay_dragging", False))
        self._sample_preview_cache_key: Optional[Tuple[str, float, int, int]] = None
        self._sample_preview_cache_img: Optional[Image.Image] = None
        self._sample_preview_content_rect: Tuple[int, int, int, int] = (0, 0, 1, 1)
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
        self._import_selected_dirs: List[Path] = []
        self._import_stop_mode: Optional[str] = None
        self._import_paused: bool = False
        self._group_stop_mode: Optional[str] = None
        self._group_paused: bool = False
        self._wm_settings_by_file: Dict[str, Dict[str, int]] = {}
        raw_applied = load_json(self.wm_settings_path, {})
        self._wm_applied_settings_by_file: Dict[str, Dict[str, int]] = {}
        if isinstance(raw_applied, dict):
            for k, v in raw_applied.items():
                if not isinstance(k, str):
                    continue
                rotate = 0
                opacity = 100
                if isinstance(v, dict):
                    rotate = int(v.get("rotate", 0))
                    opacity = int(v.get("opacity", 100))
                else:
                    rotate = int(v)
                self._wm_applied_settings_by_file[k] = {
                    "rotate": rotate % 360,
                    "opacity": max(1, min(100, opacity)),
                }
        self._wm_selector_last_file: str = ""
        self._wm_selector_changing = False
        self._internal_group_watermark_editing = False
        raw_group_wm = self.app_settings.get("internal_group_watermark_enabled", {})
        self._internal_group_watermark_enabled_map: Dict[str, bool] = {}
        if isinstance(raw_group_wm, dict):
            for k, v in raw_group_wm.items():
                if isinstance(k, str):
                    self._internal_group_watermark_enabled_map[k] = bool(v)

        self.tabs = QTabWidget()
        self.setCentralWidget(self.tabs)

        self.import_tab = QWidget()
        self.group_tab = QWidget()
        self.wm_tab = QWidget()

        self.tabs.addTab(self.import_tab, "1) 导入图片")
        self.tabs.addTab(self.wm_tab, "2) 添加水印")
        self.tabs.addTab(self.group_tab, "3) 分组管理")
        self.tabs.currentChanged.connect(self.on_tab_changed)

        self._build_import_tab()
        self._build_group_tab()
        self._build_wm_tab()
        self.refresh_watermark_lists()
        # Force initial preview render: show base image even without watermark.
        self.update_preview()

    def _migrate_legacy_data_layout(self) -> None:
        legacy_to_new = {
            self.data_root / "internal_library": self.workspace_root / "internal_library",
            self.data_root / "second_layer": self.workspace_root / "second_layer",
            self.data_root / "export_images": self.workspace_root / "export_images",
            self.data_root / "watermark_library": self.workspace_root / "watermark_library",
            self.data_root / "assets": self.workspace_root / "assets",
        }
        ensure_dir(self.workspace_root)
        for old_dir, new_dir in legacy_to_new.items():
            try:
                if old_dir.exists() and not new_dir.exists():
                    old_dir.rename(new_dir)
            except Exception:
                # Ignore migration errors and continue with current layout.
                continue
        old_settings = self.data_root / "app_settings.json"
        new_settings = self.workspace_root / "app_settings.json"
        try:
            if old_settings.exists() and not new_settings.exists():
                old_settings.rename(new_settings)
            elif old_settings.exists() and new_settings.exists():
                old_settings.unlink()
        except Exception:
            pass

    def _normalize_md5_index_paths(self) -> None:
        if not isinstance(self.md5_index, dict):
            self.md5_index = {}
            return
        normed: Dict[str, str] = {}
        changed = False
        for k, v in self.md5_index.items():
            vv = str(v).replace("/", "\\")
            low = vv.lower()
            if low.startswith("shopsync_data\\"):
                vv = vv[len("shopsync_data\\") :]
                changed = True
            normed[str(k)] = vv
        self.md5_index = normed
        if changed:
            save_json(self.index_path, self.md5_index)

    def on_tab_changed(self, idx: int) -> None:
        # Refresh preview whenever switching to the watermark tab.
        if self.tabs.widget(idx) is self.wm_tab:
            self.update_preview()
        else:
            self._preview_debounce_timer.stop()

    def _log(self, box: QTextEdit, msg: str) -> None:
        box.append(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}")
        QApplication.processEvents()

    def _resolve_dir_dialog_start(
        self,
        *,
        explicit_path: Optional[str] = None,
        last_key: Optional[str] = None,
        default_path: Optional[str] = None,
    ) -> str:
        if explicit_path:
            p = Path(str(explicit_path).strip())
            if p.exists() and p.is_dir():
                return str(p)
        if last_key:
            m = self.app_settings.get("last_dir_map", {})
            if isinstance(m, dict):
                lp = str(m.get(last_key, "")).strip()
                if lp:
                    p = Path(lp)
                    if p.exists() and p.is_dir():
                        return str(p)
        if default_path:
            p = Path(str(default_path).strip())
            if p.exists() and p.is_dir():
                return str(p)
        return ""

    def _remember_last_dir(self, key: str, selected_dir: str) -> None:
        if not key:
            return
        m = self.app_settings.get("last_dir_map", {})
        if not isinstance(m, dict):
            m = {}
        m[key] = str(selected_dir)
        self.app_settings["last_dir_map"] = m
        save_json(self.settings_path, self.app_settings)

    def _pick_dir(
        self,
        line: QLineEdit,
        *,
        title: str = "选择文件夹",
        explicit_path: Optional[str] = None,
        last_key: Optional[str] = None,
        default_path: Optional[str] = None,
    ) -> None:
        start_dir = self._resolve_dir_dialog_start(
            explicit_path=explicit_path,
            last_key=last_key,
            default_path=default_path,
        )
        d = QFileDialog.getExistingDirectory(self, title, start_dir)
        if d:
            if hasattr(self, "src_import_line") and line is self.src_import_line:
                self._import_selected_dirs = [Path(d).resolve()]
            line.setText(d)
            if last_key:
                self._remember_last_dir(last_key, d)

    def _pick_import_dirs_multi(self) -> None:
        start_dir = self._resolve_dir_dialog_start(
            explicit_path=self.src_import_line.text().strip() if hasattr(self, "src_import_line") else "",
            last_key="import_dir_multi",
            default_path=str(self.root),
        )
        dlg = QFileDialog(self, "选择导入文件夹(可多选)", start_dir)
        dlg.setFileMode(QFileDialog.Directory)
        dlg.setOption(QFileDialog.ShowDirsOnly, True)
        dlg.setOption(QFileDialog.DontUseNativeDialog, True)
        for v in dlg.findChildren(QListView):
            v.setSelectionMode(QAbstractItemView.ExtendedSelection)
        for v in dlg.findChildren(QTreeView):
            v.setSelectionMode(QAbstractItemView.ExtendedSelection)
        if dlg.exec_() != QFileDialog.Accepted:
            return
        selected: List[Path] = []
        seen = set()
        for raw in dlg.selectedFiles():
            p = Path(raw).resolve()
            s = str(p)
            if s in seen:
                continue
            seen.add(s)
            if p.exists() and p.is_dir():
                selected.append(p)
        if not selected:
            return
        self._import_selected_dirs = selected
        if len(selected) == 1:
            self.src_import_line.setText(str(selected[0]))
            self._remember_last_dir("import_dir_multi", str(selected[0]))
        else:
            self.src_import_line.setText(f"已选择 {len(selected)} 个一级文件夹")
            self._remember_last_dir("import_dir_multi", str(selected[0].parent))

    def _pick_file(
        self,
        line: QLineEdit,
        flt: str,
        *,
        title: str = "选择文件",
        explicit_path: Optional[str] = None,
        last_key: Optional[str] = None,
        default_path: Optional[str] = None,
    ) -> None:
        explicit_dir = ""
        if explicit_path:
            ep = Path(str(explicit_path).strip())
            if ep.exists():
                explicit_dir = str(ep if ep.is_dir() else ep.parent)
        start_dir = self._resolve_dir_dialog_start(
            explicit_path=explicit_dir,
            last_key=last_key,
            default_path=default_path,
        )
        p, _ = QFileDialog.getOpenFileName(self, title, start_dir, flt)
        if p:
            line.setText(p)
            if last_key:
                self._remember_last_dir(last_key, str(Path(p).parent))

    def _normalize_row_spacing(self, row_layout: QHBoxLayout) -> None:
        # Keep row spacing consistent across the app without changing overall layout structure.
        row_layout.setContentsMargins(0, 0, 0, 0)
        row_layout.setSpacing(8)

    def _on_import_source_text_changed(self, txt: str) -> None:
        t = (txt or "").strip()
        if t.startswith("已选择 ") and "个一级文件夹" in t:
            return
        self._import_selected_dirs = []

    def _build_import_tab(self) -> None:
        layout = QVBoxLayout(self.import_tab)

        form = QFormLayout()
        self.src_import_line = QLineEdit("")
        self.src_import_line.textChanged.connect(self._on_import_source_text_changed)
        pick_btn = QPushButton("选择导入文件夹")
        pick_btn.clicked.connect(
            lambda: self._pick_dir(
                self.src_import_line,
                title="选择导入文件夹",
                explicit_path="",
                last_key="import_dir",
                default_path="",
            )
        )
        row = QHBoxLayout()
        self._normalize_row_spacing(row)
        row.addWidget(self.src_import_line)
        row.addWidget(pick_btn)
        w = QWidget()
        w.setLayout(row)
        form.addRow("选择导入文件夹", w)
        layout.addLayout(form)

        import_action_row = QHBoxLayout()
        self._normalize_row_spacing(import_action_row)
        self.btn_import_execute = QPushButton("导入")
        self.btn_import_execute.clicked.connect(self.import_to_internal_library)
        self.btn_import_pause = QPushButton("暂停")
        self.btn_import_pause.clicked.connect(self.toggle_import_pause)
        self.btn_import_stop = QPushButton("中断")
        self.btn_import_stop.clicked.connect(self.request_import_stop)
        self.btn_import_cancel = QPushButton("取消")
        self.btn_import_cancel.clicked.connect(self.request_import_cancel)
        import_action_row.addWidget(self.btn_import_execute)
        import_action_row.addWidget(self.btn_import_pause)
        import_action_row.addWidget(self.btn_import_stop)
        import_action_row.addWidget(self.btn_import_cancel)
        import_action_row.addStretch(1)
        layout.addLayout(import_action_row)
        self._set_import_action_buttons(running=False)

        group_row = QHBoxLayout()
        left_col = QVBoxLayout()
        group_header_row = QHBoxLayout()
        self._normalize_row_spacing(group_header_row)
        group_header_row.addWidget(QLabel("颜色分组"))
        self.btn_internal_group_wm_edit = QPushButton("编辑水印状态")
        self.btn_internal_group_wm_edit.setCheckable(True)
        self.btn_internal_group_wm_edit.setMinimumWidth(112)
        self.btn_internal_group_wm_edit.toggled.connect(self.toggle_internal_group_watermark_edit_mode)
        group_header_row.addStretch(1)
        group_header_row.addWidget(self.btn_internal_group_wm_edit)
        left_col.addLayout(group_header_row)
        self.internal_group_stats_label = QLabel("分组数量: 0  |  图片数量: 0")
        left_col.addWidget(self.internal_group_stats_label)
        self.internal_group_list = QListWidget()
        self.internal_group_list.setItemDelegate(InternalGroupStatusDelegate(self.internal_group_list))
        self.internal_group_list.currentItemChanged.connect(self.on_internal_group_changed)
        self.internal_group_list.itemChanged.connect(self.on_internal_group_item_changed)
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
        row_h = self.internal_file_view_mode.sizeHint().height()
        self.internal_file_view_mode.setFixedHeight(row_h)
        mode_row.addStretch(1)
        self.btn_file_select_all = QPushButton("全选")
        self.btn_file_select_all.clicked.connect(self.select_all_files)
        self.btn_file_select_all.setFixedHeight(row_h)
        mode_row.addWidget(self.btn_file_select_all)
        self.btn_file_select_none = QPushButton("取消选择")
        self.btn_file_select_none.clicked.connect(self.clear_file_selection)
        self.btn_file_select_none.setFixedHeight(row_h)
        mode_row.addWidget(self.btn_file_select_none)
        self.btn_batch_move = QPushButton("移动到其他分组")
        self.btn_batch_move.clicked.connect(self.batch_move_files)
        self.btn_batch_move.setFixedHeight(row_h)
        mode_row.addWidget(self.btn_batch_move)
        self.btn_batch_delete = QPushButton("删除")
        self.btn_batch_delete.clicked.connect(self.batch_delete_files)
        self.btn_batch_delete.setFixedHeight(row_h)
        mode_row.addWidget(self.btn_batch_delete)
        self.btn_group_refresh = QPushButton("刷新")
        self.btn_group_refresh.clicked.connect(self.refresh_internal_group_list)
        self.btn_group_refresh.setFixedHeight(row_h)
        mode_row.addWidget(self.btn_group_refresh)
        mode_wrap = QWidget()
        mode_wrap.setLayout(mode_row)
        mode_wrap.setFixedHeight(row_h)
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

    def _persist_internal_group_watermark_enabled(self) -> None:
        self.app_settings["internal_group_watermark_enabled"] = dict(self._internal_group_watermark_enabled_map)
        save_json(self.settings_path, self.app_settings)

    def _is_internal_group_watermark_enabled(self, group_name: str) -> bool:
        return bool(self._internal_group_watermark_enabled_map.get(group_name, True))

    def _set_internal_group_watermark_enabled(self, group_name: str, enabled: bool) -> None:
        self._internal_group_watermark_enabled_map[group_name] = bool(enabled)
        self._persist_internal_group_watermark_enabled()

    def _make_internal_group_item(self, group_dir: Path, image_count: int) -> QListWidgetItem:
        enabled = self._is_internal_group_watermark_enabled(group_dir.name)
        suffix = "水印" if enabled else "直出"
        item = QListWidgetItem(f"{group_dir.name} ({image_count} 图片)  [{suffix}]")
        item.setData(Qt.UserRole, str(group_dir))
        item.setData(Qt.UserRole + 1, group_dir.name)
        base_flags = Qt.ItemIsEnabled | Qt.ItemIsSelectable
        if self._internal_group_watermark_editing:
            item.setFlags(base_flags | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Checked if enabled else Qt.Unchecked)
        else:
            item.setFlags(base_flags)
            item.setData(Qt.CheckStateRole, None)
        return item

    def _apply_internal_group_watermark_edit_mode_to_list(self) -> None:
        if not hasattr(self, "internal_group_list"):
            return
        base_flags = Qt.ItemIsEnabled | Qt.ItemIsSelectable
        self.internal_group_list.blockSignals(True)
        for i in range(self.internal_group_list.count()):
            item = self.internal_group_list.item(i)
            if item is None:
                continue
            group_name = str(item.data(Qt.UserRole + 1) or "")
            enabled = self._is_internal_group_watermark_enabled(group_name)
            if self._internal_group_watermark_editing:
                item.setFlags(base_flags | Qt.ItemIsUserCheckable)
                item.setCheckState(Qt.Checked if enabled else Qt.Unchecked)
            else:
                item.setData(Qt.CheckStateRole, None)
                item.setFlags(base_flags)
            self._sync_internal_group_watermark_item(item)
        self.internal_group_list.blockSignals(False)
        self.internal_group_list.viewport().update()

    def toggle_internal_group_watermark_edit_mode(self, checked: bool) -> None:
        self._internal_group_watermark_editing = bool(checked)
        if hasattr(self, "btn_internal_group_wm_edit"):
            self.btn_internal_group_wm_edit.setText("完成水印状态编辑" if checked else "编辑水印状态")
        self._apply_internal_group_watermark_edit_mode_to_list()

    def _sync_internal_group_watermark_item(self, item: Optional[QListWidgetItem]) -> None:
        if item is None:
            return
        group_name = str(item.data(Qt.UserRole + 1) or "")
        if not group_name:
            return
        if self._internal_group_watermark_editing and bool(item.flags() & Qt.ItemIsUserCheckable):
            enabled = item.checkState() == Qt.Checked
        else:
            enabled = self._is_internal_group_watermark_enabled(group_name)
        suffix = "水印" if enabled else "直出"
        text = item.text()
        if "  [" in text:
            text = text.split("  [", 1)[0]
        item.setText(f"{text}  [{suffix}]")

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
            it = self._make_internal_group_item(d, cnt)
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
            it = self._make_internal_group_item(d, cnt)
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
            if name in self._internal_group_watermark_enabled_map:
                self._internal_group_watermark_enabled_map.pop(name, None)
                self._persist_internal_group_watermark_enabled()
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

    def on_internal_group_item_changed(self, item: QListWidgetItem) -> None:
        if item is None or not bool(item.flags() & Qt.ItemIsUserCheckable):
            return
        group_name = str(item.data(Qt.UserRole + 1) or "")
        if not group_name:
            return
        enabled = item.checkState() == Qt.Checked
        self._set_internal_group_watermark_enabled(group_name, enabled)
        self.internal_group_list.blockSignals(True)
        self._sync_internal_group_watermark_item(item)
        self.internal_group_list.blockSignals(False)

    def on_internal_group_changed(self, current: Optional[QListWidgetItem], previous: Optional[QListWidgetItem]) -> None:
        _ = previous
        if current is not None:
            self._sync_internal_group_watermark_item(current)
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
        self._thumb_schedule_timer.stop()
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

    def _start_thumbnail_worker(self, tasks: List[Tuple[int, Path]]) -> None:
        if not tasks:
            return
        self._stop_thumbnail_worker()
        thread = QThread(self)
        worker = ThumbnailWorker(tasks, thumb_w=self.thumb_w, thumb_h=self.thumb_h)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.thumbnail_ready.connect(self._on_thumbnail_ready)
        worker.finished.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.start()
        self._thumb_worker_thread = thread
        self._thumb_worker = worker

    def _visible_thumbnail_rows(self) -> List[int]:
        view = self.internal_group_file_list
        if view.count() <= 0:
            return []
        viewport = view.viewport().rect()
        rows: List[int] = []
        for row in range(view.count()):
            it = view.item(row)
            if it is None:
                continue
            r = view.visualItemRect(it)
            if not r.isValid() or r.isEmpty():
                continue
            if r.intersects(viewport):
                rows.append(row)
        return rows

    def _thumbnail_priority_rows(self, pending_rows: List[int]) -> List[int]:
        if not pending_rows:
            return []
        pending_set = set(pending_rows)
        visible = [r for r in self._visible_thumbnail_rows() if r in pending_set]
        if not visible:
            return sorted(pending_rows)
        ordered: List[int] = []
        ordered.extend(sorted(visible))
        vmin = min(visible)
        vmax = max(visible)
        up = vmin - 1
        down = vmax + 1
        while up >= 0 or down < len(self._thumb_files):
            if up >= 0 and up in pending_set:
                ordered.append(up)
            if down < len(self._thumb_files) and down in pending_set:
                ordered.append(down)
            up -= 1
            down += 1
        return ordered

    def _schedule_thumbnail_loading(self) -> None:
        if self.internal_file_view_mode.currentText() != "缩略图视图":
            return
        if not self._thumb_files:
            return
        pending_rows = [i for i in range(len(self._thumb_files)) if i not in self._thumb_loaded_rows]
        if not pending_rows:
            self._stop_thumbnail_worker()
            return
        priority_rows = self._thumbnail_priority_rows(pending_rows)
        tasks = [(row, self._thumb_files[row]) for row in priority_rows]
        self._start_thumbnail_worker(tasks)

    def _on_thumbnail_viewport_changed(self, _value: int) -> None:
        self._thumb_schedule_timer.start(80)

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
        self._thumb_loaded_rows.add(row)

    def populate_internal_group_files(self, group_dir: Path) -> None:
        self._stop_thumbnail_worker()
        self._thumb_files = []
        self._thumb_loaded_rows = set()
        try:
            self.internal_group_file_list.verticalScrollBar().valueChanged.disconnect(self._on_thumbnail_viewport_changed)
        except Exception:
            pass
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
            self._thumb_files = files
            self.internal_group_file_list.verticalScrollBar().valueChanged.connect(self._on_thumbnail_viewport_changed)
            QApplication.processEvents()
            self._schedule_thumbnail_loading()
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
        src_text = self.src_import_line.text().strip() if hasattr(self, "src_import_line") else ""
        if not src_text:
            QMessageBox.warning(self, "提示", "请选择导入文件夹后再导入")
            return
        self._import_stop_mode = None
        self._import_paused = False
        feature_import_to_internal_library(
            self,
        )

    def _set_import_action_buttons(self, running: bool) -> None:
        if hasattr(self, "btn_import_execute"):
            self.btn_import_execute.setEnabled(not running)
        if hasattr(self, "btn_import_pause"):
            self.btn_import_pause.setEnabled(running)
            self.btn_import_pause.setText("暂停")
        if hasattr(self, "btn_import_stop"):
            self.btn_import_stop.setEnabled(running)
        if hasattr(self, "btn_import_cancel"):
            self.btn_import_cancel.setEnabled(running)

    def toggle_import_pause(self) -> None:
        self._import_paused = not bool(self._import_paused)
        if hasattr(self, "btn_import_pause"):
            self.btn_import_pause.setText("继续" if self._import_paused else "暂停")
        self._log(self.import_log, f"导入任务：{'已暂停' if self._import_paused else '已继续'}")

    def request_import_stop(self) -> None:
        self._import_stop_mode = "stop"
        self._log(self.import_log, "导入任务：已请求中断")

    def request_import_cancel(self) -> None:
        self._import_stop_mode = "cancel"
        self._log(self.import_log, "导入任务：已请求取消并回滚")

    def _build_group_tab(self) -> None:
        layout = QVBoxLayout(self.group_tab)

        cfg_row = QHBoxLayout()
        self._normalize_row_spacing(cfg_row)
        self.group_count = QSpinBox()
        self.group_count.setRange(1, 2000)
        self.group_count.setValue(1)
        self.group_count.setFixedWidth(84)
        self.group_wm_selector = QComboBox()
        self.group_wm_selector.setFixedWidth(120)
        self.group_cfg_selector = QComboBox()
        self.group_cfg_selector.setFixedWidth(120)

        cfg_row.addWidget(QLabel("分组数量"))
        cfg_row.addWidget(self.group_count)
        cfg_row.addWidget(QLabel("水印"))
        cfg_row.addWidget(self.group_wm_selector)
        cfg_row.addWidget(QLabel("配置"))
        cfg_row.addWidget(self.group_cfg_selector)
        cfg_row.addStretch(1)
        self.btn_build_second = QPushButton("随机抽取(合成)")
        self.btn_build_second.clicked.connect(self.build_second_layer)
        cfg_row.addWidget(self.btn_build_second)
        self.btn_build_second_pause = QPushButton("暂停")
        self.btn_build_second_pause.clicked.connect(self.toggle_group_pause)
        cfg_row.addWidget(self.btn_build_second_pause)
        self.btn_build_second_stop = QPushButton("中断")
        self.btn_build_second_stop.clicked.connect(self.request_group_stop)
        cfg_row.addWidget(self.btn_build_second_stop)
        self.btn_build_second_cancel = QPushButton("取消")
        self.btn_build_second_cancel.clicked.connect(self.request_group_cancel)
        cfg_row.addWidget(self.btn_build_second_cancel)
        layout.addLayout(cfg_row)
        self._set_group_action_buttons(running=False)

        self.second_line = QLineEdit(str(self.second_root))
        self.export_line = QLineEdit(str(self.export_root))
        self.export_config_selector = QComboBox()
        cache_row = QHBoxLayout()
        self._normalize_row_spacing(cache_row)
        cache_row.addWidget(QLabel("缓存路径"))
        cache_row.addWidget(self.second_line, 1)
        btn_pick_cache = QPushButton("选择缓存路径")
        btn_pick_cache.clicked.connect(
            lambda: self._pick_dir(
                self.second_line,
                title="选择缓存路径",
                explicit_path=self.second_line.text().strip(),
                last_key="cache_dir",
                default_path=str(self.second_root),
            )
        )
        cache_row.addWidget(btn_pick_cache)
        layout.addLayout(cache_row)

        export_row = QHBoxLayout()
        self._normalize_row_spacing(export_row)
        export_row.addWidget(QLabel("导出路径"))
        export_row.addWidget(self.export_line, 1)
        btn_pick_export = QPushButton("选择导出路径")
        btn_pick_export.clicked.connect(
            lambda: self._pick_dir(
                self.export_line,
                title="选择导出路径",
                explicit_path=self.export_line.text().strip(),
                last_key="export_dir",
                default_path=str(self.export_root),
            )
        )
        export_row.addWidget(btn_pick_export)
        layout.addLayout(export_row)

        group_row = QHBoxLayout()
        left_col = QVBoxLayout()
        left_col.setSpacing(6)
        left_col.addWidget(QLabel("最终分组"))
        self.second_group_stats_label = QLabel("分组数量: 0  |  图片数量: 0")
        left_col.addWidget(self.second_group_stats_label)
        second_batch_row = QHBoxLayout()
        self._normalize_row_spacing(second_batch_row)
        self.btn_second_batch_manage = QPushButton("批量管理")
        self.btn_second_batch_manage.setCheckable(True)
        self.btn_second_batch_manage.setMinimumWidth(92)
        self.btn_second_batch_manage.toggled.connect(self.on_second_batch_manage_toggled)
        second_batch_row.addWidget(self.btn_second_batch_manage)
        second_batch_row.addStretch(1)
        left_col.addLayout(second_batch_row)

        self.second_batch_actions_wrap = QWidget()
        second_batch_actions = QGridLayout(self.second_batch_actions_wrap)
        second_batch_actions.setContentsMargins(0, 0, 0, 0)
        second_batch_actions.setHorizontalSpacing(8)
        second_batch_actions.setVerticalSpacing(6)
        self.btn_second_select_all = QPushButton("全选")
        self.btn_second_select_all.clicked.connect(self.select_all_second_groups)
        self.btn_second_select_none = QPushButton("全不选")
        self.btn_second_select_none.clicked.connect(self.clear_second_group_checks)
        self.btn_second_batch_export = QPushButton("批量导出")
        self.btn_second_batch_export.clicked.connect(self.batch_export_selected_second_groups)
        self.btn_second_batch_delete = QPushButton("批量删除")
        self.btn_second_batch_delete.clicked.connect(self.batch_delete_second_groups)
        action_h = self.btn_second_batch_manage.sizeHint().height()
        for btn in [self.btn_second_select_all, self.btn_second_select_none, self.btn_second_batch_export, self.btn_second_batch_delete]:
            btn.setMinimumHeight(action_h)
            btn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        second_batch_actions.addWidget(self.btn_second_select_all, 0, 0)
        second_batch_actions.addWidget(self.btn_second_select_none, 0, 1)
        second_batch_actions.addWidget(self.btn_second_batch_export, 1, 0)
        second_batch_actions.addWidget(self.btn_second_batch_delete, 1, 1)
        left_col.addWidget(self.second_batch_actions_wrap)
        self.second_folder_list = QListWidget()
        self.second_folder_list.currentItemChanged.connect(self.on_second_group_changed)
        self.second_folder_list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.second_folder_list.customContextMenuRequested.connect(self.on_second_group_context_menu)
        self.second_folder_list.viewport().installEventFilter(self)
        self.second_folder_list.setMinimumHeight(420)
        self.second_folder_list.setMaximumWidth(280)
        left_col.addWidget(self.second_folder_list)
        left_wrap = QWidget()
        left_wrap.setLayout(left_col)
        group_row.addWidget(left_wrap, 0)

        right_col = QVBoxLayout()
        right_col.addWidget(QLabel("分组管理"))
        right_ctrl_row = QHBoxLayout()
        self._normalize_row_spacing(right_ctrl_row)
        right_ctrl_row.addWidget(QLabel("文件视图模式"))
        self.second_file_view_mode = QComboBox()
        self.second_file_view_mode.addItems(["列表视图", "缩略图视图"])
        self.second_file_view_mode.currentIndexChanged.connect(self.on_second_file_view_mode_changed)
        last_mode = self.app_settings.get(
            "second_file_view_mode",
            self.app_settings.get("internal_file_view_mode", "缩略图视图"),
        )
        idx = self.second_file_view_mode.findText(str(last_mode))
        if idx >= 0:
            self.second_file_view_mode.setCurrentIndex(idx)
        right_ctrl_row.addWidget(self.second_file_view_mode)
        row_h = self.second_file_view_mode.sizeHint().height()
        self.second_file_view_mode.setFixedHeight(row_h)
        right_ctrl_row.addStretch(1)
        self.btn_second_group_refresh = QPushButton("刷新")
        self.btn_second_group_refresh.clicked.connect(self.refresh_export_folder_list)
        self.btn_second_group_refresh.setFixedHeight(row_h)
        right_ctrl_row.addWidget(self.btn_second_group_refresh)
        ctrl_wrap = QWidget()
        ctrl_wrap.setLayout(right_ctrl_row)
        ctrl_wrap.setFixedHeight(row_h)
        right_col.addWidget(ctrl_wrap)

        self.second_group_file_list = QListWidget()
        self.second_group_file_list.setMinimumHeight(420)
        self.second_group_file_list.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.second_group_file_list.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.second_group_file_list.setHorizontalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.second_group_file_list.verticalScrollBar().setSingleStep(12)
        self.second_group_file_list.setStyleSheet(
            "QListWidget::item { padding: 2px 4px; }"
            "QListWidget::item:selected { background: #cfe8ff; color: #111; }"
        )
        self.second_group_file_list.itemDoubleClicked.connect(self.on_second_file_double_clicked)
        right_col.addWidget(self.second_group_file_list)
        right_wrap = QWidget()
        right_wrap.setLayout(right_col)
        group_row.addWidget(right_wrap, 1)

        layout.addLayout(group_row, 1)

        log_row = QVBoxLayout()
        log_row.addWidget(QLabel("分组管理日志"))
        self.group_log = QTextEdit()
        self.group_log.setReadOnly(True)
        self.group_log.setMaximumHeight(150)
        log_row.addWidget(self.group_log)
        layout.addLayout(log_row, 0)

        self.export_log = QTextEdit()
        self.export_log.setReadOnly(True)
        self._second_batch_mode = False
        self._set_second_batch_controls_visible(False)
        self.refresh_export_folder_list()
        self.refresh_export_config_list()

    def _collect_second_group_info(self) -> Tuple[List[Tuple[Path, int]], int]:
        groups: List[Tuple[Path, int]] = []
        total_images = 0
        second_root = Path(self.second_line.text().strip()) if hasattr(self, "second_line") else self.second_root
        if not second_root.exists():
            return groups, total_images
        for d in sorted([p for p in second_root.iterdir() if p.is_dir()]):
            cnt = len([p for p in d.iterdir() if is_image_file(p)])
            groups.append((d, cnt))
            total_images += cnt
        return groups, total_images

    def _update_second_group_stats(self) -> None:
        if not hasattr(self, "second_group_stats_label"):
            return
        groups, total_images = self._collect_second_group_info()
        self.second_group_stats_label.setText(f"分组数量: {len(groups)}  |  图片数量: {total_images}")

    def on_second_group_changed(self, current: Optional[QListWidgetItem], _previous: Optional[QListWidgetItem]) -> None:
        if not hasattr(self, "second_group_file_list"):
            return
        self.second_group_file_list.clear()
        if current is None:
            return
        p = current.data(Qt.UserRole)
        if not p:
            return
        group_dir = Path(str(p))
        if not group_dir.exists() or not group_dir.is_dir():
            return
        mode = self.second_file_view_mode.currentText() if hasattr(self, "second_file_view_mode") else "列表视图"
        imgs = [x for x in sorted(group_dir.iterdir()) if is_image_file(x)]
        if mode == "缩略图视图":
            icon_size = QSize(self.thumb_w, self.thumb_h)
            self.second_group_file_list.setViewMode(QListView.IconMode)
            self.second_group_file_list.setIconSize(icon_size)
            self.second_group_file_list.setGridSize(QSize(self.thumb_grid_w, self.thumb_grid_h))
            self.second_group_file_list.setResizeMode(QListView.Adjust)
            self.second_group_file_list.setMovement(QListView.Static)
            self.second_group_file_list.setWordWrap(True)
            self.second_group_file_list.setSpacing(10)
            self.second_group_file_list.setTextElideMode(Qt.ElideRight)
            self.second_group_file_list.setWrapping(True)
            self.second_group_file_list.setFlow(QListView.LeftToRight)
            self.second_group_file_list.setUniformItemSizes(False)
            placeholder = self.style().standardIcon(QStyle.SP_FileIcon)
            for pth in imgs:
                try:
                    qpix = QPixmap(str(pth))
                    icon = placeholder
                    if not qpix.isNull():
                        icon = QIcon(qpix.scaled(icon_size, Qt.KeepAspectRatio, Qt.SmoothTransformation))
                    it = QListWidgetItem(icon, pth.name)
                    it.setTextAlignment(Qt.AlignHCenter)
                    it.setData(Qt.UserRole, str(pth))
                    self.second_group_file_list.addItem(it)
                except Exception:
                    continue
            return
        self.second_group_file_list.setViewMode(QListView.ListMode)
        self.second_group_file_list.setIconSize(QSize(22, 22))
        self.second_group_file_list.setGridSize(QSize())
        self.second_group_file_list.setResizeMode(QListView.Fixed)
        self.second_group_file_list.setMovement(QListView.Static)
        self.second_group_file_list.setWordWrap(False)
        self.second_group_file_list.setSpacing(2)
        self.second_group_file_list.setUniformItemSizes(True)
        file_icon = self.style().standardIcon(QStyle.SP_FileIcon)
        for pth in imgs:
            try:
                st = pth.stat()
                size_kb = st.st_size / 1024.0
                mtime = datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M")
                text = f"{pth.name}    {size_kb:.1f} KB    {mtime}"
            except Exception:
                text = pth.name
            it = QListWidgetItem(file_icon, text)
            it.setData(Qt.UserRole, str(pth))
            self.second_group_file_list.addItem(it)

    def on_second_file_view_mode_changed(self, _idx: int) -> None:
        self.app_settings["second_file_view_mode"] = self.second_file_view_mode.currentText()
        save_json(self.settings_path, self.app_settings)
        self.on_second_group_changed(self.second_folder_list.currentItem(), None)

    def on_second_group_context_menu(self, pos) -> None:
        if getattr(self, "_second_batch_mode", False):
            return
        item = self.second_folder_list.itemAt(pos)
        if item is None:
            return
        raw = item.data(Qt.UserRole)
        if not raw:
            return
        group_dir = Path(str(raw))
        if not group_dir.exists() or not group_dir.is_dir():
            return
        menu = QMenu(self)
        act_open = menu.addAction("打开文件夹")
        act_export = menu.addAction("导出")
        act_delete = menu.addAction("删除")
        chosen = menu.exec_(self.second_folder_list.mapToGlobal(pos))
        if chosen == act_open:
            self.open_second_group_folder(group_dir)
            return
        if chosen == act_export:
            self.export_second_group(group_dir, item)
            return
        if chosen == act_delete:
            self.delete_second_group(group_dir)
            return

    def open_second_group_folder(self, group_dir: Path) -> None:
        try:
            os.startfile(str(group_dir))
        except Exception as e:
            QMessageBox.warning(self, "提示", f"打开失败: {type(e).__name__}: {e}")

    def export_second_group(self, group_dir: Path, item: Optional[QListWidgetItem] = None) -> None:
        if not group_dir.exists() or not group_dir.is_dir():
            QMessageBox.warning(self, "提示", "分组不存在")
            return
        if item is None:
            for i in range(self.second_folder_list.count()):
                it = self.second_folder_list.item(i)
                if str(it.data(Qt.UserRole)) == str(group_dir):
                    item = it
                    break
        # Right-click single export should stay in single-select mode and never leave checkboxes visible.
        base_flags = Qt.ItemIsEnabled | Qt.ItemIsSelectable
        for i in range(self.second_folder_list.count()):
            it = self.second_folder_list.item(i)
            it.setData(Qt.CheckStateRole, None)
            it.setFlags(base_flags)
        self.second_folder_list.clearSelection()
        if item is not None:
            item.setSelected(True)
            self.second_folder_list.setCurrentItem(item)
        self.second_folder_list.viewport().update()
        self.batch_export()
        self._log(self.group_log, f"导出分组: {group_dir.name}")

    def delete_second_group(self, group_dir: Path) -> None:
        ans = QMessageBox.question(
            self,
            "删除分组",
            f"确定删除 {group_dir.name} 分组吗？将会删除该分组及其所有图片",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if ans != QMessageBox.Yes:
            return
        try:
            shutil.rmtree(group_dir)
            self._log(self.group_log, f"删除分组: {group_dir.name}")
            self.refresh_export_folder_list()
        except Exception as e:
            QMessageBox.critical(self, "错误", f"{type(e).__name__}: {e}")

    def on_second_file_double_clicked(self, item: QListWidgetItem) -> None:
        if item is None:
            return
        raw = item.data(Qt.UserRole)
        if not raw:
            return
        p = Path(str(raw))
        if p.exists() and p.is_file():
            self.view_internal_file(p)

    def _set_second_batch_controls_visible(self, visible: bool) -> None:
        if not hasattr(self, "btn_second_select_all"):
            return
        if hasattr(self, "second_batch_actions_wrap"):
            self.second_batch_actions_wrap.setVisible(visible)
            return
        self.btn_second_select_all.setVisible(visible)
        self.btn_second_select_none.setVisible(visible)
        self.btn_second_batch_export.setVisible(visible)
        self.btn_second_batch_delete.setVisible(visible)

    def _apply_second_batch_mode_to_list(self, checked_paths: Optional[List[str]] = None) -> None:
        checked = set(checked_paths or [])
        if not hasattr(self, "second_folder_list"):
            return
        base_flags = Qt.ItemIsEnabled | Qt.ItemIsSelectable
        for i in range(self.second_folder_list.count()):
            it = self.second_folder_list.item(i)
            if self._second_batch_mode:
                it.setFlags(base_flags | Qt.ItemIsUserCheckable)
                raw = str(it.data(Qt.UserRole))
                it.setCheckState(Qt.Checked if raw in checked else Qt.Unchecked)
            else:
                it.setData(Qt.CheckStateRole, None)
                it.setFlags(base_flags)
        self.second_folder_list.viewport().update()

    def _checked_second_group_dirs(self) -> List[Path]:
        out: List[Path] = []
        if not hasattr(self, "second_folder_list"):
            return out
        for i in range(self.second_folder_list.count()):
            it = self.second_folder_list.item(i)
            if not bool(it.flags() & Qt.ItemIsUserCheckable):
                continue
            if it.checkState() == Qt.Checked:
                raw = it.data(Qt.UserRole)
                if not raw:
                    continue
                p = Path(str(raw))
                if p.exists() and p.is_dir():
                    out.append(p)
        return out

    def on_second_batch_manage_toggled(self, checked: bool) -> None:
        self._second_batch_mode = bool(checked)
        self._set_second_batch_controls_visible(self._second_batch_mode)
        if self._second_batch_mode:
            self._apply_second_batch_mode_to_list(checked_paths=[])
        else:
            self.refresh_export_folder_list()
            self._apply_second_batch_mode_to_list()
            self.second_folder_list.clearSelection()
            self.second_folder_list.viewport().update()
        self._log(self.group_log, "开启批量管理" if checked else "关闭批量管理")

    def eventFilter(self, obj, event):
        if (
            hasattr(self, "second_folder_list")
            and obj is self.second_folder_list.viewport()
            and getattr(self, "_second_batch_mode", False)
            and event.type() == QEvent.MouseButtonRelease
            and event.button() == Qt.LeftButton
        ):
            item = self.second_folder_list.itemAt(event.pos())
            if item is not None and bool(item.flags() & Qt.ItemIsUserCheckable):
                item.setCheckState(Qt.Unchecked if item.checkState() == Qt.Checked else Qt.Checked)
                self.second_folder_list.setCurrentItem(item)
                return True
        return super().eventFilter(obj, event)

    def select_all_second_groups(self) -> None:
        if not self._second_batch_mode:
            return
        for i in range(self.second_folder_list.count()):
            it = self.second_folder_list.item(i)
            if bool(it.flags() & Qt.ItemIsUserCheckable):
                it.setCheckState(Qt.Checked)

    def clear_second_group_checks(self) -> None:
        if not self._second_batch_mode:
            return
        for i in range(self.second_folder_list.count()):
            it = self.second_folder_list.item(i)
            if bool(it.flags() & Qt.ItemIsUserCheckable):
                it.setCheckState(Qt.Unchecked)

    def batch_export_selected_second_groups(self) -> None:
        if not self._second_batch_mode:
            return
        selected_dirs = self._checked_second_group_dirs()
        if not selected_dirs:
            QMessageBox.information(self, "提示", "请先勾选要导出的分组")
            return
        self.batch_export()
        self._log(self.group_log, f"批量导出分组: {len(selected_dirs)} 个")

    def _open_or_activate_folder(self, folder: Path) -> None:
        if folder is None:
            return
        p = Path(folder)
        if not p.exists():
            return
        key = str(p.resolve()).rstrip("\\/")
        if not hasattr(self, "_explorer_hwnd_by_path"):
            self._explorer_hwnd_by_path: Dict[str, int] = {}

        # 1) Try cached window handle first.
        hwnd = int(self._explorer_hwnd_by_path.get(key, 0) or 0)
        if hwnd and self._activate_window_by_hwnd(hwnd):
            return

        # 2) Find an existing Explorer window for this path.
        hwnd = self._find_explorer_hwnd_by_path(p)
        if hwnd and self._activate_window_by_hwnd(hwnd):
            self._explorer_hwnd_by_path[key] = hwnd
            return

        # 3) Not found: open once, then capture handle for next time.
        try:
            subprocess.Popen(["explorer", str(p)])
        except Exception:
            try:
                os.startfile(str(p))
            except Exception:
                return

        deadline = time.time() + 2.0
        while time.time() < deadline:
            hwnd = self._find_explorer_hwnd_by_path(p)
            if hwnd:
                self._explorer_hwnd_by_path[key] = hwnd
                break
            time.sleep(0.08)

    @staticmethod
    def _activate_window_by_hwnd(hwnd: int) -> bool:
        try:
            user32 = ctypes.windll.user32
            if not user32.IsWindow(int(hwnd)):
                return False
            SW_RESTORE = 9
            user32.ShowWindow(int(hwnd), SW_RESTORE)
            # SetForegroundWindow may return False due system foreground lock policy;
            # treat a valid window handle as success to avoid reopening duplicate windows.
            try:
                user32.SetForegroundWindow(int(hwnd))
            except Exception:
                pass
            return True
        except Exception:
            return False

    def _find_explorer_hwnd_by_path(self, folder: Path) -> int:
        target = str(Path(folder).resolve())
        ps_script = r"""
$target = [System.IO.Path]::GetFullPath($env:CODEX_TARGET_PATH).TrimEnd('\')
try {
  $shell = New-Object -ComObject Shell.Application
} catch {
  exit 1
}
foreach ($w in $shell.Windows()) {
  try {
    $doc = $w.Document
    if ($null -eq $doc -or $null -eq $doc.Folder) { continue }
    $cur = [string]$doc.Folder.Self.Path
    if ([string]::IsNullOrWhiteSpace($cur)) { continue }
    $cur = [System.IO.Path]::GetFullPath($cur).TrimEnd('\')
    if ($cur -ieq $target) {
      [Console]::WriteLine([string]$w.HWND)
      exit 0
    }
  } catch {}
}
exit 1
"""
        try:
            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            encoded = base64.b64encode(ps_script.encode("utf-16le")).decode("ascii")
            env = dict(os.environ)
            env["CODEX_TARGET_PATH"] = target
            res = subprocess.run(
                ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-EncodedCommand", encoded],
                capture_output=True,
                text=True,
                creationflags=flags,
                timeout=2.5,
                env=env,
            )
            if res.returncode != 0:
                return 0
            out = (res.stdout or "").strip()
            return int(out) if out.isdigit() else 0
        except Exception:
            return 0

    def batch_delete_second_groups(self) -> None:
        if not self._second_batch_mode:
            return
        dirs = self._checked_second_group_dirs()
        if not dirs:
            QMessageBox.information(self, "提示", "请先勾选要删除的分组")
            return
        ans = QMessageBox.question(
            self,
            "批量删除分组",
            f"确定删除 {len(dirs)} 个分组吗？将会删除分组及其所有图片",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if ans != QMessageBox.Yes:
            return
        deleted = 0
        for d in dirs:
            try:
                shutil.rmtree(d)
                deleted += 1
            except Exception:
                continue
        self.refresh_export_folder_list()
        self._log(self.group_log, f"批量删除分组: {deleted}/{len(dirs)} 个")

    def pick_candidates(self) -> None:
        return

    def _on_group_size_min_changed(self, v: int) -> None:
        if v > self.per_group_max.value():
            self.per_group_max.setValue(v)

    def _on_group_size_max_changed(self, v: int) -> None:
        if v < self.per_group_min.value():
            self.per_group_min.setValue(v)

    def _resolve_candidate_item_path(self, item: QListWidgetItem) -> Optional[Path]:
        return feature_resolve_candidate_item_path(self, item)

    def build_second_layer(self) -> None:
        self._group_stop_mode = None
        self._group_paused = False
        feature_build_second_layer(
            self,
            watermark_config_cls=WatermarkConfig,
        )

    def _set_group_action_buttons(self, running: bool) -> None:
        if hasattr(self, "btn_build_second"):
            self.btn_build_second.setEnabled(not running)
        if hasattr(self, "btn_build_second_pause"):
            self.btn_build_second_pause.setEnabled(running)
            self.btn_build_second_pause.setText("暂停")
        if hasattr(self, "btn_build_second_stop"):
            self.btn_build_second_stop.setEnabled(running)
        if hasattr(self, "btn_build_second_cancel"):
            self.btn_build_second_cancel.setEnabled(running)

    def toggle_group_pause(self) -> None:
        self._group_paused = not bool(self._group_paused)
        if hasattr(self, "btn_build_second_pause"):
            self.btn_build_second_pause.setText("继续" if self._group_paused else "暂停")
        self._log(self.group_log, f"抽取合成任务：{'已暂停' if self._group_paused else '已继续'}")

    def request_group_stop(self) -> None:
        self._group_stop_mode = "stop"
        self._log(self.group_log, "抽取合成任务：已请求中断")

    def request_group_cancel(self) -> None:
        self._group_stop_mode = "cancel"
        self._log(self.group_log, "抽取合成任务：已请求取消并回滚")

    def _resolve_default_preview_image(self) -> Path:
        # Prefer Qt embedded resource, extract to writable runtime path for PIL usage.
        dst = resolve_data_path("shopsync_data", "assets", "images", "默认预览底图.jpg")
        qrc_path = ":/assets/images/default_preview.jpg"
        if not is_valid_image(dst):
            extract_qrc_file_to_path(qrc_path, dst)
        if is_valid_image(dst):
            return dst
        # Fallbacks for non-packaged source runs.
        p = resolve_resource_path("shopsync_data", "assets", "images", "默认预览底图.jpg")
        if is_valid_image(p):
            return p
        p2 = resolve_data_path("shopsync_data", "assets", "images", "默认预览底图.jpg")
        return p2

    def _build_wm_tab(self) -> None:
        layout = QHBoxLayout(self.wm_tab)
        self.default_preview_image = self._resolve_default_preview_image()

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
        pick_wm.clicked.connect(
            lambda: self._pick_file(
                self.watermark_file_line,
                "PNG Files (*.png)",
                title="选择PNG水印文件",
                explicit_path=self.watermark_file_line.text().strip(),
                last_key="wm_png_dir",
                default_path="",
            )
        )
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
        setting_layout = QGridLayout(setting_box)
        setting_layout.setContentsMargins(9, 9, 9, 9)
        setting_layout.setHorizontalSpacing(8)
        setting_layout.setVerticalSpacing(8)

        self.wm_selector = QComboBox()
        self.wm_selector.setIconSize(QSize(52, 52))
        wm_view = QListView()
        wm_view.setIconSize(QSize(52, 52))
        wm_view.setSpacing(4)
        self.wm_selector.setView(wm_view)
        self.wm_selector.setItemDelegate(WatermarkComboDelegate(self.wm_selector))
        self.wm_selector.currentIndexChanged.connect(self._on_wm_selector_changed)

        self.wm_rotate_slider = self._make_slider(-999999, 999999, 0, self.update_preview)
        self.wm_opacity_slider = self._make_slider(1, 100, 100, self.update_preview)
        self.wm_rotate_slider.valueChanged.connect(self._on_wm_setting_value_changed)
        self.wm_opacity_slider.valueChanged.connect(self._on_wm_setting_value_changed)
        self.wm_rotate_slider.editingFinished.connect(self._on_wm_rotate_edit_finished)
        self.show_border_checkbox = QCheckBox("显示边框")
        self.show_border_checkbox.setChecked(False)
        self.show_border_checkbox.toggled.connect(self.update_preview)
        row_h = self.wm_rotate_slider.sizeHint().height()
        self.wm_rotate_slider.setFixedHeight(row_h)
        self.wm_opacity_slider.setFixedHeight(row_h)
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
        setting_layout.setRowMinimumHeight(0, row_h)
        setting_layout.setRowMinimumHeight(1, row_h)
        setting_layout.setRowMinimumHeight(2, row_h)

        btn_apply = QPushButton("应用")
        btn_apply.clicked.connect(self.apply_wm_settings)
        btn_reset = QPushButton("重置")
        btn_reset.clicked.connect(self.reset_wm_settings)
        btn_delete = QPushButton("删除")
        btn_delete.clicked.connect(self.delete_selected_watermark)
        wm_btn_row = QHBoxLayout()
        self._normalize_row_spacing(wm_btn_row)
        wm_btn_row.addWidget(btn_apply)
        wm_btn_row.addWidget(btn_reset)
        wm_btn_row.addWidget(btn_delete)
        wm_btn_wrap = QWidget()
        wm_btn_wrap.setLayout(wm_btn_row)
        wm_btn_wrap.setFixedHeight(row_h)
        setting_layout.addWidget(wm_btn_wrap, 3, 1, 1, 3)
        setting_layout.setRowMinimumHeight(3, row_h)
        setting_box.setFixedHeight(setting_box.sizeHint().height())
        left_col.addWidget(setting_box)

        cfg_box = QGroupBox("配置设置")
        cfg_box.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        cfg_box.setFixedHeight(362)
        cfg_layout = QGridLayout(cfg_box)

        self.cfg_name = QLineEdit("")
        self.final_x_slider = self._make_slider(-100, 200, 50, self.update_preview)
        self.final_y_slider = self._make_slider(-100, 200, 50, self.update_preview)
        self.final_scale_slider = self._make_slider(5, 100, 25, self.update_preview)
        self.final_rotate_slider = self._make_slider(-999999, 999999, 0, self.update_preview)
        self.final_opacity_slider = self._make_slider(1, 100, 100, self.update_preview)
        self.final_rotate_slider.editingFinished.connect(self._on_final_rotate_edit_finished)
        self.final_constrain_inside_checkbox = QCheckBox("限制水印在图像范围内")
        self.final_constrain_inside_checkbox.setChecked(True)
        self.final_constrain_inside_checkbox.toggled.connect(self._on_constrain_inside_toggled)
        self.config_selector = QComboBox()
        self.config_selector.currentIndexChanged.connect(self.load_selected_config)
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
        cfg_layout.addWidget(self.final_constrain_inside_checkbox, 6, 1, 1, 3)
        cfg_layout.addWidget(QLabel("配置列表"), 7, 0)
        cfg_layout.addWidget(self.config_selector, 7, 1, 1, 3)

        cfg_btn_row = QHBoxLayout()
        self._normalize_row_spacing(cfg_btn_row)
        save_btn = QPushButton("保存配置")
        save_btn.clicked.connect(self.save_current_config)
        reset_cfg_btn = QPushButton("重置")
        reset_cfg_btn.clicked.connect(self.reset_config_settings)
        delete_cfg_btn = QPushButton("删除")
        delete_cfg_btn.clicked.connect(self.delete_selected_config)
        rename_cfg_btn = QPushButton("重命名")
        rename_cfg_btn.clicked.connect(self.rename_selected_config)
        cfg_btn_row.addWidget(save_btn)
        cfg_btn_row.addWidget(reset_cfg_btn)
        cfg_btn_row.addWidget(delete_cfg_btn)
        cfg_btn_row.addWidget(rename_cfg_btn)
        cfg_btn_wrap = QWidget()
        cfg_btn_wrap.setLayout(cfg_btn_row)
        cfg_layout.addWidget(cfg_btn_wrap, 8, 1, 1, 3)

        sample_row = QHBoxLayout()
        self._normalize_row_spacing(sample_row)
        self.sample_image_line = QLineEdit(str(self.default_preview_image))
        pick_sample = QPushButton("选择预览图片")
        pick_sample.clicked.connect(lambda: self._pick_file(self.sample_image_line, "Image Files (*.png *.jpg *.jpeg *.webp *.bmp)"))
        export_preview_btn = QPushButton("导出预览图")
        export_preview_btn.clicked.connect(self.export_composited_preview_image)
        sample_row.addWidget(self.sample_image_line, 1)
        sample_row.addWidget(pick_sample)
        sample_row.addWidget(export_preview_btn)
        sample_wrap = QWidget()
        sample_wrap.setLayout(sample_row)
        cfg_layout.addWidget(QLabel("预览图片"), 9, 0)
        cfg_layout.addWidget(sample_wrap, 9, 1, 1, 3)

        left_col.addWidget(cfg_box)

        self.preview_label = OverlayPreviewLabel("预览效果")
        self.preview_label.setAlignment(Qt.AlignCenter)
        self.preview_label.setMinimumHeight(560)
        self.preview_label.setStyleSheet("border: 1px solid #999;")
        wm_show_box = QGroupBox("水印预览")
        wm_show_layout = QVBoxLayout(wm_show_box)
        wm_show_layout.setContentsMargins(4, 8, 4, 4)
        wm_show_layout.setSpacing(4)
        self.current_wm_preview = OverlayPreviewLabel("当前水印预览")
        self.current_wm_preview.setAlignment(Qt.AlignCenter)
        self.current_wm_preview.setMinimumHeight(96)
        self.current_wm_preview.setMaximumHeight(96)
        self.current_wm_preview.setStyleSheet("border: 1px solid #999;")
        self.preview_label.overlayDragged.connect(self._on_effect_overlay_dragged)
        self.preview_label.overlayDragStateChanged.connect(self._on_effect_overlay_drag_state_changed)
        self.current_wm_name = QLabel("当前水印名称")
        self.current_wm_name.setAlignment(Qt.AlignCenter)
        self.current_wm_name.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        self.current_wm_name.setFixedHeight(22)
        wm_show_layout.addWidget(self.current_wm_preview)
        wm_show_layout.addWidget(self.current_wm_name)
        wm_show_box.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        wm_show_box.setFixedHeight(148)
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
        return self._overlay_dragging

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

    def _save_wm_settings_for_file(self, wm_file: str) -> None:
        if not wm_file:
            return
        self._wm_settings_by_file[wm_file] = {
            "rotate": int(self.wm_rotate_slider.value()),
            "opacity": int(self.wm_opacity_slider.value()),
        }

    def _save_applied_wm_settings_for_file(self, wm_file: str, rotate: int, opacity: int) -> None:
        if not wm_file:
            return
        self._wm_applied_settings_by_file[wm_file] = {
            "rotate": self._normalize_degree(rotate),
            "opacity": max(1, min(100, int(opacity))),
        }
        self._persist_wm_applied_settings()

    def _persist_wm_applied_settings(self) -> None:
        save_json(self.wm_settings_path, self._wm_applied_settings_by_file)

    def _get_applied_wm_rotate(self, wm_file: str) -> int:
        if not wm_file:
            return 0
        data = self._wm_applied_settings_by_file.get(wm_file, {})
        return self._normalize_degree(int(data.get("rotate", 0)))

    def _get_applied_wm_opacity(self, wm_file: str) -> int:
        if not wm_file:
            return 100
        data = self._wm_applied_settings_by_file.get(wm_file, {})
        return max(1, min(100, int(data.get("opacity", 100))))

    def _effective_wm_rotate_for_file(self, wm_file: str, delta_override: Optional[int] = None) -> int:
        base_rotate = self._get_applied_wm_rotate(wm_file)
        if delta_override is None:
            selected = self._selected_wm_filename()
            if wm_file and wm_file == selected:
                delta = int(self.wm_rotate_slider.value())
            else:
                delta = int(self._wm_settings_by_file.get(wm_file, {}).get("rotate", 0))
        else:
            delta = int(delta_override)
        return self._normalize_degree(base_rotate + delta)

    def _effective_wm_opacity_for_file(self, wm_file: str, delta_override: Optional[int] = None) -> int:
        if delta_override is None:
            selected = self._selected_wm_filename()
            if wm_file and wm_file == selected:
                value = int(self.wm_opacity_slider.value())
            else:
                value = int(self._wm_settings_by_file.get(wm_file, {}).get("opacity", self._get_applied_wm_opacity(wm_file)))
        else:
            value = int(delta_override)
        return max(1, min(100, value))

    def _restore_wm_settings_for_file(self, wm_file: str) -> None:
        if not wm_file:
            return
        data = self._wm_settings_by_file.get(wm_file)
        if data is None:
            data = {"rotate": 0, "opacity": self._get_applied_wm_opacity(wm_file)}
            self._wm_settings_by_file[wm_file] = dict(data)
        self.wm_rotate_slider.blockSignals(True)
        self.wm_opacity_slider.blockSignals(True)
        self.wm_rotate_slider.setValue(int(data.get("rotate", 0)))
        self.wm_opacity_slider.setValue(int(data.get("opacity", 100)))
        self.wm_rotate_slider.blockSignals(False)
        self.wm_opacity_slider.blockSignals(False)

    def _on_wm_setting_value_changed(self, _value: int) -> None:
        if self._wm_selector_changing:
            return
        wm_file = self._selected_wm_filename()
        if wm_file:
            self._save_wm_settings_for_file(wm_file)

    def _reset_wm_settings_to_last_applied(self, wm_file: str) -> None:
        if not wm_file:
            self._reset_wm_setting_sliders()
            return
        applied_opacity = self._get_applied_wm_opacity(wm_file)
        self.wm_rotate_slider.blockSignals(True)
        self.wm_opacity_slider.blockSignals(True)
        self.wm_rotate_slider.setValue(0)
        self.wm_opacity_slider.setValue(applied_opacity)
        self.wm_rotate_slider.blockSignals(False)
        self.wm_opacity_slider.blockSignals(False)
        self._save_wm_settings_for_file(wm_file)

    def delete_selected_watermark(self) -> None:
        feature_delete_selected_watermark(self)

    def _selected_wm_filename(self) -> str:
        data = self.wm_selector.currentData()
        if isinstance(data, str) and data:
            return data
        return self.wm_selector.currentText().strip()

    def _make_wm_selector_icon(self, p: Path, size: int = 52) -> QIcon:
        try:
            with Image.open(p) as im:
                rgba = im.convert("RGBA")
            applied_rotate = self._get_applied_wm_rotate(p.name)
            if applied_rotate:
                rgba = rotate_keep_scale(rgba, applied_rotate, resample=Image.Resampling.BICUBIC)
            pix = pil_to_qpixmap(rgba)
            if pix.isNull():
                return QIcon()
            pix = pix.scaled(size, size, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            return QIcon(pix)
        except Exception:
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

    def _get_cached_sample_preview(self, sample_path: Path, tw: int, th: int) -> Tuple[Image.Image, Tuple[int, int, int, int]]:
        st = sample_path.stat()
        key = (str(sample_path), st.st_mtime, tw, th)
        if self._sample_preview_cache_key == key and self._sample_preview_cache_img is not None:
            return self._sample_preview_cache_img.copy(), self._sample_preview_content_rect

        with Image.open(sample_path) as img:
            src = img.convert("RGB")
            fitted = ImageOps.contain(src, (tw, th), method=Image.Resampling.LANCZOS)
            canvas = Image.new("RGB", (tw, th), (32, 32, 32))
            x = (tw - fitted.width) // 2
            y = (th - fitted.height) // 2
            canvas.paste(fitted, (x, y))
        self._sample_preview_cache_key = key
        self._sample_preview_cache_img = canvas
        self._sample_preview_content_rect = (x, y, fitted.width, fitted.height)
        return canvas.copy(), self._sample_preview_content_rect

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
        wm_file = self._selected_wm_filename()
        mark = wm_img.convert("RGBA")
        rotate_resample = Image.Resampling.BILINEAR if fast_mode else Image.Resampling.BICUBIC
        mark = rotate_keep_scale(mark, self._effective_wm_rotate_for_file(wm_file), resample=rotate_resample)
        op = self._effective_wm_opacity_for_file(wm_file)
        if op < 100:
            alpha = mark.getchannel("A").point(lambda x: int(x * op / 100))
            mark.putalpha(alpha)
        return mark

    @staticmethod
    def _normalize_degree(v: int) -> int:
        return int(v) % 360

    def _normalize_degree_spin(self, spin: QSpinBox) -> None:
        deg = self._normalize_degree(spin.value())
        if spin.value() == deg:
            return
        spin.blockSignals(True)
        spin.setValue(deg)
        spin.blockSignals(False)

    def _build_effect_mark(
        self,
        base: Image.Image,
        wm_layer: Image.Image,
        *,
        fast_mode: bool,
        final_rotate_override: Optional[int],
        scale_base_width: Optional[int] = None,
        wm_setting_rotate_deg: int = 0,
        wm_setting_opacity_pct: int = 100,
    ) -> Image.Image:
        mark = wm_layer.convert("RGBA")
        resize_resample = Image.Resampling.BILINEAR if fast_mode else Image.Resampling.LANCZOS
        rotate_resample = Image.Resampling.BICUBIC
        # Keep the same transform order as apply_watermark():
        # wm setting rotate/opacity -> config scale -> config rotate -> config opacity.
        base_rotate = int(wm_setting_rotate_deg) % 360
        if base_rotate:
            mark = rotate_keep_scale(mark, base_rotate, resample=rotate_resample)
        wm_opacity = max(1, min(100, int(wm_setting_opacity_pct)))
        if wm_opacity < 100:
            alpha = mark.getchannel("A").point(lambda x: int(x * wm_opacity / 100))
            mark.putalpha(alpha)

        base_w_for_scale = int(scale_base_width) if scale_base_width is not None else int(base.width)
        base_w_for_scale = max(1, base_w_for_scale)
        target_w = max(1, int(base_w_for_scale * self.final_scale_slider.value() / 100))
        ratio = target_w / max(1, mark.width)
        target_h = max(1, int(mark.height * ratio))
        mark = mark.resize((target_w, target_h), resize_resample)
        rotate_deg = self.final_rotate_slider.value() if final_rotate_override is None else int(final_rotate_override)
        mark = rotate_keep_scale(mark, self._normalize_degree(int(rotate_deg)), resample=rotate_resample)
        final_opacity = max(1, min(100, int(self.final_opacity_slider.value())))
        op = int(round(final_opacity))
        op = max(1, min(100, op))
        if op < 100:
            alpha = mark.getchannel("A").point(lambda x: int(x * op / 100))
            mark.putalpha(alpha)
        return mark

    @staticmethod
    def _calc_pct_range_for_bounds(mark_len: int, bounds_len: int, constrain_inside: bool) -> Tuple[int, int]:
        b = max(1, int(bounds_len))
        m = max(1, int(mark_len))
        if constrain_inside:
            min_pct = int(round((m * 50.0) / b))
            max_pct = int(round(100.0 - (m * 50.0) / b))
        else:
            min_pct = int(round(-(m * 50.0) / b))
            max_pct = int(round(100.0 + (m * 50.0) / b))
        if min_pct > max_pct:
            mid = int(round((min_pct + max_pct) / 2.0))
            return (mid, mid)
        return (min_pct, max_pct)

    def _sync_xy_slider_ranges(
        self,
        base_img: Image.Image,
        wm_layer: Image.Image,
        base_content_rect: Tuple[int, int, int, int],
        *,
        fast_mode: bool,
        preserve_center_px: Optional[Tuple[float, float]] = None,
        wm_setting_rotate_deg: int = 0,
        wm_setting_opacity_pct: int = 100,
    ) -> None:
        constrain_inside = self.final_constrain_inside_checkbox.isChecked()
        mark = self._build_effect_mark(
            base_img,
            wm_layer,
            fast_mode=fast_mode,
            final_rotate_override=None,
            scale_base_width=base_content_rect[2],
            wm_setting_rotate_deg=wm_setting_rotate_deg,
            wm_setting_opacity_pct=wm_setting_opacity_pct,
        )
        if constrain_inside:
            _, _, bw_fit, bh_fit = base_content_rect
            if mark.width > max(1, bw_fit) or mark.height > max(1, bh_fit):
                fit_scale = min(
                    max(1, bw_fit) / float(max(1, mark.width)),
                    max(1, bh_fit) / float(max(1, mark.height)),
                )
                nw = max(1, int(round(mark.width * fit_scale)))
                nh = max(1, int(round(mark.height * fit_scale)))
                mark = mark.resize((nw, nh), Image.Resampling.LANCZOS)
        _, _, bw, bh = (
            base_content_rect
            if constrain_inside
            else (0, 0, base_img.width, base_img.height)
        )
        x_min, x_max = self._calc_pct_range_for_bounds(mark.width, bw, constrain_inside)
        y_min, y_max = self._calc_pct_range_for_bounds(mark.height, bh, constrain_inside)
        target_x = self.final_x_slider.value()
        target_y = self.final_y_slider.value()
        if preserve_center_px is not None:
            cx, cy = preserve_center_px
            bx, by, bw2, bh2 = (
                base_content_rect
                if constrain_inside
                else (0, 0, base_img.width, base_img.height)
            )
            target_x = int(round((cx - bx) * 100.0 / float(max(1, bw2))))
            target_y = int(round((cy - by) * 100.0 / float(max(1, bh2))))
        target_x = max(x_min, min(x_max, target_x))
        target_y = max(y_min, min(y_max, target_y))
        self.final_x_slider.blockSignals(True)
        self.final_y_slider.blockSignals(True)
        self.final_x_slider.setRange(x_min, x_max)
        self.final_y_slider.setRange(y_min, y_max)
        self.final_x_slider.setValue(target_x)
        self.final_y_slider.setValue(target_y)
        self.final_x_slider.blockSignals(False)
        self.final_y_slider.blockSignals(False)

    def _compose_effect_preview(
        self,
        base_img: Image.Image,
        wm_layer: Image.Image,
        base_content_rect: Optional[Tuple[int, int, int, int]] = None,
        fast_mode: bool = False,
        use_alpha_bbox: bool = False,
        final_rotate_override: Optional[int] = None,
        constrain_bounds: Optional[Tuple[int, int, int, int]] = None,
        wm_setting_rotate_deg: int = 0,
        wm_setting_opacity_pct: int = 100,
    ) -> Tuple[Image.Image, Tuple[float, float, float, float]]:
        # Preview-only composition: simulate two layers (base + watermark), no file write.
        base = base_img.convert("RGBA")
        mark = self._build_effect_mark(
            base,
            wm_layer,
            fast_mode=fast_mode,
            final_rotate_override=final_rotate_override,
            scale_base_width=(base_content_rect[2] if base_content_rect else base.width),
            wm_setting_rotate_deg=wm_setting_rotate_deg,
            wm_setting_opacity_pct=wm_setting_opacity_pct,
        )

        constrain_inside = True
        if hasattr(self, "final_constrain_inside_checkbox"):
            constrain_inside = self.final_constrain_inside_checkbox.isChecked()
        mark, x, y = place_mark(
            base.width,
            base.height,
            mark,
            self.final_x_slider.value(),
            self.final_y_slider.value(),
            constrain_inside,
            constrain_bounds=constrain_bounds,
        )

        wm_overlay = Image.new("RGBA", base.size, (0, 0, 0, 0))
        wm_overlay.paste(mark, (x, y), mark)
        out = Image.alpha_composite(base, wm_overlay)
        if use_alpha_bbox:
            bbox = mark.getchannel("A").getbbox()
            if bbox:
                l, t, r, b = bbox
                ax = x + l
                ay = y + t
                aw = max(1, r - l)
                ah = max(1, b - t)
            else:
                ax, ay, aw, ah = x, y, max(1, mark.width), max(1, mark.height)
        else:
            ax, ay, aw, ah = x, y, max(1, mark.width), max(1, mark.height)
        bx = ax / float(max(1, base.width))
        by = ay / float(max(1, base.height))
        bw = aw / float(max(1, base.width))
        bh = ah / float(max(1, base.height))
        return out, self._clamp_norm_rect((bx, by, bw, bh))

    def _get_cached_simulated_wm(self, wm_path: Path, fast_mode: bool) -> Image.Image:
        st = wm_path.stat()
        wm_file = wm_path.name
        eff_rotate = self._effective_wm_rotate_for_file(wm_file)
        eff_opacity = self._effective_wm_opacity_for_file(wm_file)
        key = (
            str(wm_path),
            st.st_mtime,
            eff_rotate,
            eff_opacity,
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
        use_alpha_bbox: bool = False,
    ) -> Tuple[QPixmap, Tuple[float, float, float, float]]:
        key = (cache_tag, target_w, target_h, use_alpha_bbox)
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

        if use_alpha_bbox:
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
        if self._wm_selector_last_file:
            self._save_wm_settings_for_file(self._wm_selector_last_file)
        self._wm_selector_changing = True
        try:
            current_file = self._selected_wm_filename()
            self._restore_wm_settings_for_file(current_file)
            self._wm_selector_last_file = current_file
        finally:
            self._wm_selector_changing = False
        # New watermark should start with current frame aligned to actual frame.
        self._wm_current_border_norm = None
        self._fx_current_border_norm = None
        self.update_preview()

    def _on_effect_overlay_dragged(self, dx_norm: float, dy_norm: float) -> None:
        base_w = 0
        base_h = 0
        if self._sample_preview_cache_img is not None:
            base_w = int(self._sample_preview_cache_img.width)
            base_h = int(self._sample_preview_cache_img.height)
        bounds_w = base_w
        bounds_h = base_h
        if hasattr(self, "final_constrain_inside_checkbox") and self.final_constrain_inside_checkbox.isChecked():
            _, _, bw, bh = self._sample_preview_content_rect
            bounds_w = int(bw)
            bounds_h = int(bh)
        if bounds_w <= 0:
            bounds_w = 1
        if bounds_h <= 0:
            bounds_h = 1
        if base_w <= 0:
            base_w = bounds_w
        if base_h <= 0:
            base_h = bounds_h
        pct_step_x = 100.0 * (float(base_w) / float(bounds_w))
        pct_step_y = 100.0 * (float(base_h) / float(bounds_h))
        self._overlay_drag_accum_x += dx_norm * pct_step_x
        self._overlay_drag_accum_y += dy_norm * pct_step_y
        step_x = int(self._overlay_drag_accum_x) if self._overlay_drag_accum_x >= 0 else -int(-self._overlay_drag_accum_x)
        step_y = int(self._overlay_drag_accum_y) if self._overlay_drag_accum_y >= 0 else -int(-self._overlay_drag_accum_y)
        if step_x == 0 and step_y == 0:
            return
        self._overlay_drag_accum_x -= step_x
        self._overlay_drag_accum_y -= step_y
        x = self.final_x_slider.value() + step_x
        y = self.final_y_slider.value() + step_y
        x = max(self.final_x_slider.minimum(), min(self.final_x_slider.maximum(), x))
        y = max(self.final_y_slider.minimum(), min(self.final_y_slider.maximum(), y))
        changed = False
        if x != self.final_x_slider.value():
            self.final_x_slider.blockSignals(True)
            self.final_x_slider.setValue(x)
            self.final_x_slider.blockSignals(False)
            changed = True
        if y != self.final_y_slider.value():
            self.final_y_slider.blockSignals(True)
            self.final_y_slider.setValue(y)
            self.final_y_slider.blockSignals(False)
            changed = True
        if changed:
            self.request_preview_update()

    def _on_effect_overlay_drag_state_changed(self, dragging: bool) -> None:
        self._overlay_dragging = bool(dragging)
        if dragging:
            self._overlay_drag_accum_x = 0.0
            self._overlay_drag_accum_y = 0.0
            self._overlay_drag_idle_timer.stop()
        else:
            self._overlay_drag_accum_x = 0.0
            self._overlay_drag_accum_y = 0.0
            # Keep fast mode briefly while inertia-like queued updates are rendered.
            self._overlay_drag_idle_timer.start(80)

    def _on_wm_rotate_edit_finished(self) -> None:
        self._normalize_degree_spin(self.wm_rotate_slider)
        self.request_preview_update()

    def _on_final_rotate_edit_finished(self) -> None:
        self._normalize_degree_spin(self.final_rotate_slider)
        self.request_preview_update()

    def _on_constrain_inside_toggled(self, checked: bool) -> None:
        # Remap current center to new constraint space to avoid tiny jumps on toggle.
        wm_name = self._selected_wm_filename()
        if not wm_name:
            self.request_preview_update()
            return
        wm_path = self.watermark_root / wm_name
        if not wm_path.exists():
            self.request_preview_update()
            return
        try:
            sample_path = Path(self.sample_image_line.text().strip()) if self.sample_image_line.text().strip() else self.default_preview_image
            if not sample_path.exists() and self.default_preview_image.exists():
                sample_path = self.default_preview_image
            if not sample_path.exists():
                self.request_preview_update()
                return
            tw = max(1, self.preview_label.width() - 8)
            th = max(1, self.preview_label.height() - 8)
            base_preview, base_content_rect = self._get_cached_sample_preview(sample_path, tw, th)
            wm_processed = self._get_cached_simulated_wm(wm_path, fast_mode=True)
            old_constrain = not bool(checked)
            old_bounds = (
                base_content_rect
                if old_constrain
                else (0, 0, base_preview.width, base_preview.height)
            )
            mark_old = self._build_effect_mark(
                base_preview,
                wm_processed,
                fast_mode=True,
                final_rotate_override=None,
                scale_base_width=base_content_rect[2],
            )
            mark_old, old_x, old_y = place_mark(
                base_preview.width,
                base_preview.height,
                mark_old,
                self.final_x_slider.value(),
                self.final_y_slider.value(),
                old_constrain,
                constrain_bounds=old_bounds,
            )
            center_px = (old_x + mark_old.width * 0.5, old_y + mark_old.height * 0.5)
            self._sync_xy_slider_ranges(
                base_preview,
                wm_processed,
                base_content_rect,
                fast_mode=True,
                preserve_center_px=center_px,
            )
        except Exception:
            pass
        self.request_preview_update()

    def refresh_watermark_lists(self) -> None:
        feature_refresh_watermark_lists(self)

    def current_cfg_obj(self) -> Optional[WatermarkConfig]:
        return feature_current_cfg_obj(self, WatermarkConfig)

    def save_current_config(self) -> None:
        feature_save_current_config(self, watermark_config_cls=WatermarkConfig)

    def load_selected_config(self) -> None:
        feature_load_selected_config(self)

    def reset_config_settings(self) -> None:
        # Prefer restoring last saved values from selected config; fallback to defaults.
        cfg_name = self.config_selector.currentText().strip() if hasattr(self, "config_selector") else ""
        data = None
        reset_source = "默认值"
        if cfg_name:
            data = load_json(self.config_root / f"{cfg_name}.json", None)
            if isinstance(data, dict):
                reset_source = f"已保存配置 {cfg_name}"
        if not data:
            name_from_input = self.cfg_name.text().strip()
            if name_from_input:
                data = load_json(self.config_root / f"{name_from_input}.json", None)
                if isinstance(data, dict):
                    reset_source = f"已保存配置 {name_from_input}"

        if isinstance(data, dict):
            self.cfg_name.setText(str(data.get("name", cfg_name or self.cfg_name.text().strip())))
            self.final_x_slider.setValue(int(data.get("x_pct", 50)))
            self.final_y_slider.setValue(int(data.get("y_pct", 50)))
            self.final_scale_slider.setValue(int(data.get("scale_pct", 25)))
            self.final_rotate_slider.setValue(int(data.get("rotate_deg", 0)))
            self.final_opacity_slider.setValue(int(data.get("opacity_pct", 100)))
            if hasattr(self, "final_constrain_inside_checkbox"):
                self.final_constrain_inside_checkbox.setChecked(bool(data.get("constrain_inside", True)))
        else:
            self.final_x_slider.setValue(50)
            self.final_y_slider.setValue(50)
            self.final_scale_slider.setValue(25)
            self.final_rotate_slider.setValue(0)
            self.final_opacity_slider.setValue(100)
            if hasattr(self, "final_constrain_inside_checkbox"):
                self.final_constrain_inside_checkbox.setChecked(True)
        self._log(self.wm_log, f"配置已重置: {reset_source}")
        self.update_preview()

    def delete_selected_config(self) -> None:
        name = self.config_selector.currentText().strip()
        if not name:
            QMessageBox.information(self, "提示", "请先选择配置")
            return
        cfg_path = self.config_root / f"{name}.json"
        if not cfg_path.exists():
            QMessageBox.warning(self, "错误", "配置文件不存在")
            return
        ans = QMessageBox.question(
            self,
            "删除配置",
            f"确认删除配置“{name}”吗？",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if ans != QMessageBox.Yes:
            return
        try:
            cfg_path.unlink()
            self._log(self.wm_log, f"已删除配置: {name}")
            self.refresh_watermark_lists()
            self.refresh_export_config_list()
        except Exception as e:
            QMessageBox.critical(self, "删除失败", f"{type(e).__name__}: {e}")

    def rename_selected_config(self) -> None:
        old_name = self.config_selector.currentText().strip()
        if not old_name:
            QMessageBox.information(self, "提示", "请先选择配置")
            return
        new_name, ok = QInputDialog.getText(self, "重命名配置", "新配置名称：", text=old_name)
        if not ok:
            return
        new_name = new_name.strip()
        if not new_name:
            QMessageBox.warning(self, "错误", "配置名称不能为空")
            return
        invalid = set('<>:"/\\|?*')
        if any(ch in invalid for ch in new_name):
            QMessageBox.warning(self, "错误", "配置名称包含非法字符")
            return
        if new_name == old_name:
            return
        old_path = self.config_root / f"{old_name}.json"
        new_path = self.config_root / f"{new_name}.json"
        if not old_path.exists():
            QMessageBox.warning(self, "错误", "原配置文件不存在")
            return
        if new_path.exists():
            QMessageBox.warning(self, "错误", f"目标配置已存在: {new_name}")
            return
        try:
            data = load_json(old_path, {})
            if isinstance(data, dict):
                data["name"] = new_name
                save_json(new_path, data)
                old_path.unlink()
            else:
                old_path.rename(new_path)
            self._log(self.wm_log, f"配置重命名: {old_name} -> {new_name}")
            self.refresh_watermark_lists()
            idx = self.config_selector.findText(new_name)
            if idx >= 0:
                self.config_selector.setCurrentIndex(idx)
            self.refresh_export_config_list()
        except Exception as e:
            QMessageBox.critical(self, "重命名失败", f"{type(e).__name__}: {e}")

    def export_composited_preview_image(self) -> None:
        sample_path = Path(self.sample_image_line.text().strip()) if self.sample_image_line.text().strip() else self.default_preview_image
        if not sample_path.exists() and self.default_preview_image.exists():
            sample_path = self.default_preview_image
            self.sample_image_line.setText(str(sample_path))
        if not sample_path.exists():
            QMessageBox.warning(self, "错误", "预览图片不存在")
            return

        out_path_str, _ = QFileDialog.getSaveFileName(
            self,
            "导出合成预览图",
            str(self.export_root / f"preview_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jpg"),
            "JPEG Files (*.jpg *.jpeg);;PNG Files (*.png)",
        )
        if not out_path_str:
            return
        out_path = Path(out_path_str)

        try:
            with Image.open(sample_path) as s_im:
                base = s_im.convert("RGB")

            wm_name = self._selected_wm_filename()
            if wm_name:
                wm_path = self.watermark_root / wm_name
                if wm_path.exists():
                    wm_raw = self._get_cached_wm_raw(wm_path)
                    wm_rotate = self._effective_wm_rotate_for_file(wm_name)
                    wm_opacity = self._effective_wm_opacity_for_file(wm_name)
                    composed, _ = self._compose_effect_preview(
                        base,
                        wm_raw,
                        fast_mode=False,
                        use_alpha_bbox=False,
                        final_rotate_override=None,
                        constrain_bounds=(0, 0, base.width, base.height),
                        wm_setting_rotate_deg=wm_rotate,
                        wm_setting_opacity_pct=wm_opacity,
                    )
                else:
                    composed = base.convert("RGBA")
            else:
                composed = base.convert("RGBA")

            ensure_dir(out_path.parent)
            if out_path.suffix.lower() == ".png":
                composed.save(out_path, "PNG")
            else:
                if out_path.suffix.lower() not in {".jpg", ".jpeg"}:
                    out_path = out_path.with_suffix(".jpg")
                composed.convert("RGB").save(out_path, "JPEG", quality=95, optimize=True)
            self._log(self.wm_log, f"已导出预览图: {out_path.name}")
            self._open_or_activate_folder(out_path.parent)
        except Exception as e:
            QMessageBox.critical(self, "导出失败", f"{type(e).__name__}: {e}")

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
                self.preview_label.setText("未找到默认预览缩略图.jpg，请手动选择预览缩略图")
                self._preview_rendering = False
                return

        try:
            tw = max(1, self.preview_label.width() - 8)
            th = max(1, self.preview_label.height() - 8)
            base_preview, base_content_rect = self._get_cached_sample_preview(sample_path, tw, th)
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
                wm_effective_rotate = self._effective_wm_rotate_for_file(wm_name)
                wm_effective_opacity = self._effective_wm_opacity_for_file(wm_name)
                self._sync_xy_slider_ranges(
                    base_preview,
                    wm_raw_actual,
                    base_content_rect,
                    fast_mode=fast_mode,
                    wm_setting_rotate_deg=wm_effective_rotate,
                    wm_setting_opacity_pct=wm_effective_opacity,
                )
                wm_stat = wm_path.stat()
                wm_thumb, wm_current_bbox_norm = self._get_cached_wm_preview_pix(
                    wm_processed_current,
                    max(1, self.current_wm_preview.width() - 2),
                    max(1, self.current_wm_preview.height() - 2),
                    cache_tag=("wm_preview_current", str(wm_path), wm_stat.st_mtime, wm_effective_rotate, wm_effective_opacity),
                    use_alpha_bbox=True,
                )
                _, wm_actual_bbox_norm = self._get_cached_wm_preview_pix(
                    wm_processed_current,
                    max(1, self.current_wm_preview.width() - 2),
                    max(1, self.current_wm_preview.height() - 2),
                    cache_tag=("wm_preview_actual", str(wm_path), wm_stat.st_mtime, wm_effective_rotate, wm_effective_opacity),
                    use_alpha_bbox=False,
                )
                self._wm_actual_border_norm = wm_actual_bbox_norm
                self._wm_current_border_norm = wm_current_bbox_norm
                if not wm_thumb.isNull():
                    self.current_wm_preview.setPixmap(wm_thumb)
                    self.current_wm_preview.setText("")
                else:
                    self.current_wm_preview.setPixmap(QPixmap())
                    self.current_wm_preview.setText("水印预览失败")
                self.current_wm_name.setText(wm_path.stem)
                # Two-layer simulation for effect preview; no persistent image data change.
                effect_preview, self._fx_current_border_norm = self._compose_effect_preview(
                    base_preview,
                    wm_raw_actual,
                    base_content_rect=base_content_rect,
                    fast_mode=fast_mode,
                    use_alpha_bbox=False,
                    final_rotate_override=None,
                    constrain_bounds=(
                        base_content_rect
                        if self.final_constrain_inside_checkbox.isChecked()
                        else (0, 0, base_preview.width, base_preview.height)
                    ),
                    wm_setting_rotate_deg=wm_effective_rotate,
                    wm_setting_opacity_pct=wm_effective_opacity,
                )
                _, self._fx_actual_border_norm = self._compose_effect_preview(
                    base_preview,
                    wm_raw_actual,
                    base_content_rect=base_content_rect,
                    fast_mode=fast_mode,
                    use_alpha_bbox=False,
                    final_rotate_override=0,
                    constrain_bounds=(
                        base_content_rect
                        if self.final_constrain_inside_checkbox.isChecked()
                        else (0, 0, base_preview.width, base_preview.height)
                    ),
                    wm_setting_rotate_deg=0,
                    wm_setting_opacity_pct=100,
                )
            else:
                self.final_x_slider.blockSignals(True)
                self.final_y_slider.blockSignals(True)
                self.final_x_slider.setRange(-100, 200)
                self.final_y_slider.setRange(-100, 200)
                self.final_x_slider.blockSignals(False)
                self.final_y_slider.blockSignals(False)
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
                None,
                self._wm_current_border_norm,
                show_frames,
                False,
            )
            self.preview_label.set_overlay_rects(
                None,
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
        btn1.clicked.connect(
            lambda: self._pick_dir(
                self.second_line,
                title="选择缓存路径",
                explicit_path=self.second_line.text().strip(),
                last_key="cache_dir",
                default_path=str(self.second_root),
            )
        )
        row1 = QHBoxLayout()
        self._normalize_row_spacing(row1)
        row1.addWidget(self.second_line)
        row1.addWidget(btn1)
        w1 = QWidget()
        w1.setLayout(row1)
        f.addRow("第二层文件夹", w1)

        self.export_line = QLineEdit(str(self.export_root))
        btn2 = QPushButton("选择导出文件夹")
        btn2.clicked.connect(
            lambda: self._pick_dir(
                self.export_line,
                title="选择导出路径",
                explicit_path=self.export_line.text().strip(),
                last_key="export_dir",
                default_path=str(self.export_root),
            )
        )
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
        prev_path = None
        checked_paths: List[str] = []
        if hasattr(self, "second_folder_list"):
            cur = self.second_folder_list.currentItem()
            if cur is not None:
                prev_path = cur.data(Qt.UserRole)
            for i in range(self.second_folder_list.count()):
                it = self.second_folder_list.item(i)
                if bool(it.flags() & Qt.ItemIsUserCheckable) and it.checkState() == Qt.Checked:
                    checked_paths.append(str(it.data(Qt.UserRole)))
        feature_refresh_export_folder_list(self)
        if hasattr(self, "_second_batch_mode") and self._second_batch_mode:
            self._apply_second_batch_mode_to_list(checked_paths=checked_paths)
        self._update_second_group_stats()
        if not hasattr(self, "second_folder_list"):
            return
        target_row = -1
        if prev_path:
            for i in range(self.second_folder_list.count()):
                it = self.second_folder_list.item(i)
                if str(it.data(Qt.UserRole)) == str(prev_path):
                    target_row = i
                    break
        if target_row < 0 and self.second_folder_list.count() > 0:
            target_row = 0
        if target_row >= 0:
            self.second_folder_list.setCurrentRow(target_row)
            self.on_second_group_changed(self.second_folder_list.currentItem(), None)
        elif hasattr(self, "second_group_file_list"):
            self.second_group_file_list.clear()

    def batch_export(self) -> None:
        feature_batch_export(
            self,
            watermark_config_cls=WatermarkConfig,
        )
        self._open_or_activate_folder(Path(self.export_line.text().strip()))


def main() -> None:
    app = QApplication(sys.argv)
    icon_path = resolve_resource_path("shopsync_data", "assets", "app.ico")
    if not icon_path.exists():
        icon_path = resolve_data_path("shopsync_data", "assets", "app.ico")
    if icon_path.exists():
        app.setWindowIcon(QIcon(str(icon_path)))
    w = MainWindow()
    w.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()

