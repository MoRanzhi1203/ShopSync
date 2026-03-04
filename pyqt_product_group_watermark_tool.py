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
from PyQt5.QtCore import QObject, QSize, Qt, QThread, QTimer, pyqtSignal
from PyQt5.QtGui import QIcon, QImage, QPixmap
from PyQt5.QtWidgets import (
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
    QVBoxLayout,
    QWidget,
    QComboBox,
    QSlider,
    QStyle,
)


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
        self.setWindowTitle(f"查看图片 - {image_path.name}")
        self.resize(1100, 760)
        self.label = QLabel()
        self.label.setAlignment(Qt.AlignCenter)
        self.label.setStyleSheet("background:#222; color:#ddd;")
        lay = QVBoxLayout(self)
        lay.addWidget(self.label)
        self._pix = QPixmap(str(image_path))
        if self._pix.isNull():
            self.label.setText("图片加载失败")
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
        self.setWindowTitle("分组排序管理")
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
        btn_count_asc = QPushButton("按数量升序")
        btn_count_desc = QPushButton("按数量降序")
        btn_reset = QPushButton("恢复默认(创建时间)")
        btn_ok = QPushButton("保存")
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
        raise RuntimeError(f"压缩失败: {src.name}")
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
        self.setWindowTitle("电商产品图分组水印工具")
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
        self._preview_windows: List[ImagePreviewDialog] = []

        self.tabs = QTabWidget()
        self.setCentralWidget(self.tabs)

        self.import_tab = QWidget()
        self.group_tab = QWidget()
        self.wm_tab = QWidget()
        self.export_tab = QWidget()

        self.tabs.addTab(self.import_tab, "1) 导入与去重")
        self.tabs.addTab(self.wm_tab, "2) 水印库与配置")
        self.tabs.addTab(self.group_tab, "3) 商品图分组")
        self.tabs.addTab(self.export_tab, "4) 批量出图")

        self._build_import_tab()
        self._build_group_tab()
        self._build_wm_tab()
        self._build_export_tab()
        self.refresh_watermark_lists()

    def _log(self, box: QTextEdit, msg: str) -> None:
        box.append(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}")
        QApplication.processEvents()

    def _pick_dir(self, line: QLineEdit) -> None:
        d = QFileDialog.getExistingDirectory(self, "选择目录", str(self.root))
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
        pick_btn = QPushButton("选择目录")
        pick_btn.clicked.connect(lambda: self._pick_dir(self.src_import_line))
        row = QHBoxLayout()
        self._normalize_row_spacing(row)
        row.addWidget(self.src_import_line)
        row.addWidget(pick_btn)
        w = QWidget()
        w.setLayout(row)
        form.addRow("待导入根目录", w)
        layout.addLayout(form)

        btn = QPushButton("开始导入并去重")
        btn.clicked.connect(self.import_to_internal_library)
        layout.addWidget(btn)

        group_row = QHBoxLayout()
        left_col = QVBoxLayout()
        left_col.addWidget(QLabel("当前分组"))
        self.internal_group_stats_label = QLabel("分组: 0  |  总图片: 0")
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
        mid_col.addWidget(QLabel("分组文件"))
        mode_row = QHBoxLayout()
        self._normalize_row_spacing(mode_row)
        mode_row.addWidget(QLabel("显示模式"))
        self.internal_file_view_mode = QComboBox()
        self.internal_file_view_mode.addItems(["详细信息", "大图标"])
        self.internal_file_view_mode.currentIndexChanged.connect(self.on_internal_file_view_mode_changed)
        last_mode = self.app_settings.get("internal_file_view_mode", "大图标")
        idx = self.internal_file_view_mode.findText(last_mode)
        if idx >= 0:
            self.internal_file_view_mode.setCurrentIndex(idx)
        mode_row.addWidget(self.internal_file_view_mode)
        self.btn_file_select_all = QPushButton("全选")
        self.btn_file_select_all.clicked.connect(self.select_all_files)
        mode_row.addWidget(self.btn_file_select_all)
        self.btn_file_select_none = QPushButton("全不选")
        self.btn_file_select_none.clicked.connect(self.clear_file_selection)
        mode_row.addWidget(self.btn_file_select_none)
        self.btn_batch_move = QPushButton("批量移动")
        self.btn_batch_move.clicked.connect(self.batch_move_files)
        mode_row.addWidget(self.btn_batch_move)
        self.btn_batch_delete = QPushButton("批量删除")
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
        log_row.addWidget(QLabel("日志输出"))
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
            QMessageBox.information(self, "提示", "当前没有可排序分组")
            return
        dlg = GroupSortManagerDialog(current, default, self._group_count_map(), self._group_ctime_map(), self)
        if dlg.exec_() != QDialog.Accepted:
            return
        order = dlg.get_order()
        self._set_custom_group_order(order)
        self.refresh_internal_group_list()

    def _update_internal_group_stats(self, group_count: int, total_images: int) -> None:
        self.internal_group_stats_label.setText(f"分组: {group_count}  |  总图片: {total_images}")
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
            it = QListWidgetItem(f"{d.name} ({cnt} 张)")
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
            it = QListWidgetItem(f"{d.name} ({cnt} 张)")
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
            act_create = menu.addAction("创建新分组")
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
        act_rename = menu.addAction("重命名分组")
        act_delete = menu.addAction("删除分组")
        act_clear_empty = menu.addAction("清除空分组") if self._is_empty_group(group_dir) else None
        menu.addSeparator()
        act_move_up = menu.addAction("排序上移")
        act_move_down = menu.addAction("排序下移")
        act_move_top = menu.addAction("排序置顶")
        act_move_bottom = menu.addAction("排序置底")
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
        name, ok = QInputDialog.getText(self, "创建新分组", "分组名称：")
        if not ok:
            return
        name = name.strip()
        if not name:
            QMessageBox.warning(self, "错误", "分组名称不能为空")
            return
        invalid = set('<>:"/\\|?*')
        if any(ch in invalid for ch in name):
            QMessageBox.warning(self, "错误", "分组名称包含非法字符")
            return
        target = self.internal_root / name
        if target.exists():
            QMessageBox.warning(self, "错误", f"分组已存在: {name}")
            return
        ensure_dir(target)
        cur_order = self._get_custom_group_order()
        if name not in cur_order:
            cur_order.append(name)
            self._set_custom_group_order(cur_order)
        self._log(self.import_log, f"已创建新分组: {name}")
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
            QMessageBox.information(self, "提示", "没有可清除的空分组")
            return
        ans = QMessageBox.question(
            self,
            "清除空分组",
            f"确认清除 {len(empty_groups)} 个空分组吗？",
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
        self._log(self.import_log, f"已清除空分组 {removed} 个")
        self.refresh_internal_group_list()

    def select_internal_group(self, group_dir: Path) -> None:
        for i in range(self.internal_group_list.count()):
            it = self.internal_group_list.item(i)
            if it and str(it.data(Qt.UserRole)) == str(group_dir):
                self.internal_group_list.setCurrentRow(i)
                return

    def rename_internal_group(self, old_dir: Path) -> None:
        old_name = old_dir.name
        new_name, ok = QInputDialog.getText(self, "重命名分组", "新分组名称：", text=old_name)
        if not ok:
            return
        new_name = new_name.strip()
        if not new_name:
            QMessageBox.warning(self, "错误", "分组名称不能为空")
            return
        invalid = set('<>:"/\\|?*')
        if any(ch in invalid for ch in new_name):
            QMessageBox.warning(self, "错误", "分组名称包含非法字符")
            return
        if new_name == old_name:
            return

        new_dir = old_dir.parent / new_name
        if new_dir.exists():
            QMessageBox.warning(self, "错误", f"目标分组已存在: {new_name}")
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
            self._log(self.import_log, f"分组重命名: {old_name} -> {new_name}，并同步重命名 {len(temp_files)} 个文件")
            self.refresh_internal_group_list()
            self.select_internal_group(new_dir)
        except Exception as e:
            QMessageBox.critical(self, "重命名失败", f"{type(e).__name__}: {e}")

    def delete_internal_group(self, group_dir: Path) -> None:
        name = group_dir.name
        ans = QMessageBox.question(
            self,
            "删除分组",
            f"确认删除分组“{name}”及其全部文件吗？此操作不可恢复。",
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
            self._log(self.import_log, f"分组已删除: {name}")
            self.refresh_internal_group_list()
        except Exception as e:
            QMessageBox.critical(self, "删除失败", f"{type(e).__name__}: {e}")

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
            QMessageBox.warning(self, "错误", "文件不存在")
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
            QMessageBox.warning(self, "提示", "没有可选的目标分组")
            return None
        names = [d.name for d in groups]
        name, ok = QInputDialog.getItem(self, "选择目标分组", "目标分组：", names, 0, False)
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
            QMessageBox.information(self, "提示", "请先选择文件")
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
            self._log(self.import_log, f"已移动 {moved} 张到分组: {target_group.name}")
            self.refresh_internal_group_list()
            self.select_internal_group(src_group if src_group.exists() else target_group)
        except Exception as e:
            QMessageBox.critical(self, "移动失败", f"{type(e).__name__}: {e}")

    def delete_internal_files(self, files: List[Path]) -> None:
        if not files:
            QMessageBox.information(self, "提示", "请先选择文件")
            return
        ans = QMessageBox.question(
            self,
            "删除文件",
            f"确认删除选中的 {len(files)} 个文件吗？此操作不可恢复。",
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
        self._log(self.import_log, f"已删除文件 {deleted} 个")
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
        if self.internal_file_view_mode.currentText() != "大图标":
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

        if mode == "大图标":
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
        self._stop_thumbnail_worker()
        super().closeEvent(event)

    def import_to_internal_library(self) -> None:
        src_root = Path(self.src_import_line.text().strip())
        if not src_root.exists():
            QMessageBox.warning(self, "错误", "导入目录不存在")
            return

        imported = 0
        skipped_dup = 0
        folders_created = 0
        compressed_count = 0
        compress_saved_bytes = 0

        children = [p for p in sorted(src_root.iterdir()) if p.is_dir()]
        if not children:
            QMessageBox.warning(self, "提示", "导入目录下没有子文件夹")
            return
        # Dedup scope = historical index + current import session.
        # This prevents duplicates that appear multiple times in one single import run.
        session_seen_md5 = set(self.md5_index.keys())

        folder_images: List[Tuple[Path, str, List[Path]]] = []
        total_images = 0
        for source_folder in children:
            base_name = source_folder.name.strip()
            if not base_name:
                continue
            images = list_images(source_folder)
            folder_images.append((source_folder, base_name, images))
            total_images += len(images)

        progress = QProgressDialog("正在导入并去重...", "", 0, max(1, total_images), self)
        progress.setWindowTitle("导入中")
        progress.setCancelButton(None)
        progress.setWindowModality(Qt.ApplicationModal)
        progress.setMinimumDuration(0)
        progress.setAutoClose(False)
        progress.setAutoReset(False)
        progress.show()
        self.setEnabled(False)
        QApplication.processEvents()

        processed = 0
        try:
            for source_folder, base_name, images in folder_images:
                if not images:
                    self._log(self.import_log, f"{source_folder.name}: 未发现图片")
                    continue

                new_items: List[Tuple[Path, str]] = []
                for img in images:
                    processed += 1
                    progress.setValue(processed)
                    progress.setLabelText(f"正在检测: {img.name} ({processed}/{total_images})")
                    QApplication.processEvents()
                    # De-dup by original image bytes before any compression/renaming.
                    h = md5_of_file(img)
                    if h in session_seen_md5:
                        skipped_dup += 1
                        continue
                    session_seen_md5.add(h)
                    new_items.append((img, h))

                if not new_items:
                    self._log(self.import_log, f"{source_folder.name}: 全部重复，跳过")
                    continue

                dst_folder = self.internal_root / base_name
                existed = dst_folder.exists()
                ensure_dir(dst_folder)
                if not existed:
                    folders_created += 1

                next_idx = next_image_index_in_folder(dst_folder, base_name)
                for src_img, h in new_items:
                    raw_ext = src_img.suffix.lower()
                    dst_name = f"{base_name}{next_idx:03d}{raw_ext}"
                    dst = dst_folder / dst_name
                    was_compressed, out_size = compress_image_to_under_2mb(src_img, dst, MAX_IMPORT_IMAGE_BYTES)
                    if was_compressed:
                        new_dst = dst.with_suffix(".jpg")
                        if new_dst != dst:
                            if new_dst.exists():
                                new_dst.unlink()
                            dst.rename(new_dst)
                            dst = new_dst
                        compressed_count += 1
                        saved = max(0, src_img.stat().st_size - out_size)
                        compress_saved_bytes += saved
                        self._log(
                            self.import_log,
                            f"{src_img.name}: 压缩完成 {src_img.stat().st_size/1024:.1f}KB -> {out_size/1024:.1f}KB",
                        )
                    self.md5_index[h] = str(dst.relative_to(self.root))
                    imported += 1
                    next_idx += 1
                    if imported % 10 == 0:
                        self.refresh_internal_group_overview()

                final_count = len([p for p in dst_folder.iterdir() if is_image_file(p)])
                if final_count < 50:
                    self._log(self.import_log, f"{base_name}: 当前总数 {final_count} (<50)，建议后续补图")
                else:
                    self._log(self.import_log, f"{base_name}: 新增 {len(new_items)} 张，当前总数 {final_count} 张")
                self.refresh_internal_group_overview()
        finally:
            self.setEnabled(True)
            progress.setValue(progress.maximum())
            progress.close()
            QApplication.processEvents()

        save_json(self.index_path, self.md5_index)
        self._log(
            self.import_log,
            f"完成: 新增 {imported}，重复跳过 {skipped_dup}，压缩 {compressed_count}，"
            f"节省 {(compress_saved_bytes/1024/1024):.2f}MB，新建内部库文件夹 {folders_created}",
        )
        self.refresh_internal_group_list()

    def _build_group_tab(self) -> None:
        layout = QVBoxLayout(self.group_tab)

        cfg = QGridLayout()
        self.group_count = QSpinBox()
        self.group_count.setRange(1, 999)
        self.group_count.setValue(3)
        self.per_group = QSpinBox()
        self.per_group.setRange(16, 20)
        self.per_group.setValue(16)
        self.second_prefix = QLineEdit("组")

        cfg.addWidget(QLabel("目标组数"), 0, 0)
        cfg.addWidget(self.group_count, 0, 1)
        cfg.addWidget(QLabel("每组图片数"), 0, 2)
        cfg.addWidget(self.per_group, 0, 3)
        cfg.addWidget(QLabel("二层文件夹前缀"), 0, 4)
        cfg.addWidget(self.second_prefix, 0, 5)
        layout.addLayout(cfg)

        btn_row = QHBoxLayout()
        self._normalize_row_spacing(btn_row)
        self.btn_pick_candidates = QPushButton("随机抽样并打乱")
        self.btn_pick_candidates.clicked.connect(self.pick_candidates)
        self.btn_build_second = QPushButton("按勾选生成商品图分组")
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
        all_images = list_images(self.internal_root)
        need = self.group_count.value() * self.per_group.value()
        if len(all_images) < need:
            QMessageBox.warning(self, "提示", f"内部库图片不足。需要 {need}，现有 {len(all_images)}")
            return

        self.candidate_images = random.sample(all_images, need)
        random.shuffle(self.candidate_images)

        self.candidate_list.clear()
        for p in self.candidate_images:
            item = QListWidgetItem(str(p.relative_to(self.root)))
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Checked)
            item.setData(Qt.UserRole, str(p))
            self.candidate_list.addItem(item)
        self._log(self.group_log, f"已生成候选 {len(self.candidate_images)} 张，可人工取消勾选后再建组")

    def _resolve_candidate_item_path(self, item: QListWidgetItem) -> Optional[Path]:
        # Prefer stored absolute path, fallback to displayed text, then fuzzy lookup by filename.
        raw = item.data(Qt.UserRole)
        if raw:
            p = Path(str(raw))
            if p.exists():
                return p

        txt = item.text().strip()
        if txt:
            p = Path(txt)
            if p.exists():
                return p
            p2 = self.root / txt
            if p2.exists():
                return p2

            # Compatibility fallback: if folder suffix differs, try locating by filename.
            name = p.name if p.name else p2.name
            if name:
                hits = list(self.internal_root.rglob(name))
                if hits:
                    return hits[0]
        return None

    def build_second_layer(self) -> None:
        checked: List[Path] = []
        for i in range(self.candidate_list.count()):
            it = self.candidate_list.item(i)
            if it.checkState() == Qt.Checked:
                resolved = self._resolve_candidate_item_path(it)
                if resolved is None:
                    self._log(self.group_log, f"跳过无效路径: {it.text()}")
                    continue
                checked.append(resolved)

        need = self.group_count.value() * self.per_group.value()
        if len(checked) < need:
            QMessageBox.warning(self, "提示", f"勾选数量不足。需要 {need}，当前 {len(checked)}")
            return

        random.shuffle(checked)
        selected = checked[:need]
        prefix = self.second_prefix.text().strip() or "组"

        base_idx = next_folder_index(self.second_root, prefix)
        cursor = 0
        created = 0
        for g in range(self.group_count.value()):
            folder_name = f"{prefix}{base_idx + g:03d}"
            out_dir = self.second_root / folder_name
            ensure_dir(out_dir)
            created += 1

            for i in range(self.per_group.value()):
                src = selected[cursor]
                cursor += 1
                ext = src.suffix.lower()
                dst = out_dir / f"{folder_name}_{i + 1:03d}{ext}"
                shutil.copy2(src, dst)

            self._log(self.group_log, f"{folder_name}: {self.per_group.value()} 张")

        self._log(self.group_log, f"完成: 生成二层文件夹 {created} 个，均为 {self.per_group.value()} 张")
        self.refresh_export_folder_list()

    def _build_wm_tab(self) -> None:
        layout = QVBoxLayout(self.wm_tab)

        top_box = QGroupBox("水印库")
        top_layout = QFormLayout(top_box)
        wm_row = QHBoxLayout()
        self._normalize_row_spacing(wm_row)
        self.watermark_file_line = QLineEdit()
        pick_wm = QPushButton("选择PNG")
        pick_wm.clicked.connect(lambda: self._pick_file(self.watermark_file_line, "PNG Files (*.png)"))
        add_wm = QPushButton("加入水印库")
        add_wm.clicked.connect(self.add_watermark_file)
        wm_row.addWidget(self.watermark_file_line)
        wm_row.addWidget(pick_wm)
        wm_row.addWidget(add_wm)
        w = QWidget()
        w.setLayout(wm_row)
        top_layout.addRow("PNG水印", w)

        self.wm_selector = QComboBox()
        self.wm_selector.currentIndexChanged.connect(self.update_preview)
        top_layout.addRow("已入库水印", self.wm_selector)
        layout.addWidget(top_box)

        cfg_box = QGroupBox("配置参数")
        cfg_layout = QGridLayout(cfg_box)

        self.cfg_name = QLineEdit("默认配置")
        self.x_slider = self._make_slider(0, 100, 50, self.update_preview)
        self.y_slider = self._make_slider(0, 100, 50, self.update_preview)
        self.scale_slider = self._make_slider(5, 100, 25, self.update_preview)
        self.rotate_slider = self._make_slider(-180, 180, 0, self.update_preview)
        self.opacity_slider = self._make_slider(1, 100, 100, self.update_preview)

        cfg_layout.addWidget(QLabel("配置名"), 0, 0)
        cfg_layout.addWidget(self.cfg_name, 0, 1, 1, 3)
        cfg_layout.addWidget(QLabel("X(%)"), 1, 0)
        cfg_layout.addWidget(self.x_slider, 1, 1, 1, 3)
        cfg_layout.addWidget(QLabel("Y(%)"), 2, 0)
        cfg_layout.addWidget(self.y_slider, 2, 1, 1, 3)
        cfg_layout.addWidget(QLabel("缩放(占图宽%)"), 3, 0)
        cfg_layout.addWidget(self.scale_slider, 3, 1, 1, 3)
        cfg_layout.addWidget(QLabel("旋转(°)"), 4, 0)
        cfg_layout.addWidget(self.rotate_slider, 4, 1, 1, 3)
        cfg_layout.addWidget(QLabel("透明度(%)"), 5, 0)
        cfg_layout.addWidget(self.opacity_slider, 5, 1, 1, 3)

        layout.addWidget(cfg_box)

        sample_row = QHBoxLayout()
        self._normalize_row_spacing(sample_row)
        self.sample_image_line = QLineEdit()
        pick_sample = QPushButton("选择预览底图")
        pick_sample.clicked.connect(lambda: self._pick_file(self.sample_image_line, "Image Files (*.png *.jpg *.jpeg *.webp *.bmp)"))
        sample_row.addWidget(self.sample_image_line)
        sample_row.addWidget(pick_sample)
        sample_w = QWidget()
        sample_w.setLayout(sample_row)
        layout.addWidget(sample_w)

        btn_row = QHBoxLayout()
        self._normalize_row_spacing(btn_row)
        self.config_selector = QComboBox()
        self.config_selector.currentIndexChanged.connect(self.load_selected_config)
        save_btn = QPushButton("保存配置")
        save_btn.clicked.connect(self.save_current_config)
        btn_row.addWidget(QLabel("配置存档"))
        btn_row.addWidget(self.config_selector)
        btn_row.addWidget(save_btn)
        layout.addLayout(btn_row)

        self.preview_label = QLabel("预览区")
        self.preview_label.setAlignment(Qt.AlignCenter)
        self.preview_label.setMinimumHeight(320)
        self.preview_label.setStyleSheet("border: 1px solid #999;")
        layout.addWidget(self.preview_label)

        self.wm_log = QTextEdit()
        self.wm_log.setReadOnly(True)
        layout.addWidget(self.wm_log)

    def _make_slider(self, min_v: int, max_v: int, default: int, callback) -> QSlider:
        s = QSlider(Qt.Horizontal)
        s.setRange(min_v, max_v)
        s.setValue(default)
        s.valueChanged.connect(callback)
        return s

    def add_watermark_file(self) -> None:
        src = Path(self.watermark_file_line.text().strip())
        if not src.exists() or src.suffix.lower() != ".png":
            QMessageBox.warning(self, "错误", "请选择有效的PNG水印文件")
            return
        dst = self.watermark_root / src.name
        shutil.copy2(src, dst)
        self._log(self.wm_log, f"已入库水印: {dst.name}")
        self.refresh_watermark_lists()

    def refresh_watermark_lists(self) -> None:
        current_wm = self.wm_selector.currentText()
        self.wm_selector.clear()
        for p in sorted(self.watermark_root.glob("*.png")):
            self.wm_selector.addItem(p.name)
        if current_wm:
            idx = self.wm_selector.findText(current_wm)
            if idx >= 0:
                self.wm_selector.setCurrentIndex(idx)

        current_cfg = self.config_selector.currentText() if hasattr(self, "config_selector") else ""
        if hasattr(self, "config_selector"):
            self.config_selector.clear()
            for p in sorted(self.config_root.glob("*.json")):
                self.config_selector.addItem(p.stem)
            if current_cfg:
                idx = self.config_selector.findText(current_cfg)
                if idx >= 0:
                    self.config_selector.setCurrentIndex(idx)

    def current_cfg_obj(self) -> Optional[WatermarkConfig]:
        wm_name = self.wm_selector.currentText().strip()
        cfg_name = self.cfg_name.text().strip()
        if not wm_name or not cfg_name:
            return None
        return WatermarkConfig(
            name=cfg_name,
            watermark_file=wm_name,
            x_pct=self.x_slider.value(),
            y_pct=self.y_slider.value(),
            scale_pct=self.scale_slider.value(),
            rotate_deg=self.rotate_slider.value(),
            opacity_pct=self.opacity_slider.value(),
        )

    def save_current_config(self) -> None:
        cfg = self.current_cfg_obj()
        if not cfg:
            QMessageBox.warning(self, "错误", "请先选择水印并填写配置名")
            return
        save_json(self.config_root / f"{cfg.name}.json", asdict(cfg))
        self._log(self.wm_log, f"配置已保存: {cfg.name}")
        self.refresh_watermark_lists()
        self.refresh_export_config_list()

    def load_selected_config(self) -> None:
        name = self.config_selector.currentText().strip()
        if not name:
            return
        data = load_json(self.config_root / f"{name}.json", None)
        if not data:
            return
        self.cfg_name.setText(data.get("name", name))
        wm_file = data.get("watermark_file", "")
        idx = self.wm_selector.findText(wm_file)
        if idx >= 0:
            self.wm_selector.setCurrentIndex(idx)
        self.x_slider.setValue(int(data.get("x_pct", 50)))
        self.y_slider.setValue(int(data.get("y_pct", 50)))
        self.scale_slider.setValue(int(data.get("scale_pct", 25)))
        self.rotate_slider.setValue(int(data.get("rotate_deg", 0)))
        self.opacity_slider.setValue(int(data.get("opacity_pct", 100)))
        self.update_preview()

    def update_preview(self) -> None:
        cfg = self.current_cfg_obj()
        if not cfg:
            self.preview_label.setText("预览区")
            return
        wm_path = self.watermark_root / cfg.watermark_file
        if not wm_path.exists():
            self.preview_label.setText("未找到水印文件")
            return

        sample_path = Path(self.sample_image_line.text().strip()) if self.sample_image_line.text().strip() else None
        if not sample_path or not sample_path.exists():
            internal_imgs = list_images(self.second_root)
            sample_path = internal_imgs[0] if internal_imgs else None
            if sample_path is None:
                self.preview_label.setText("请先选择预览底图")
                return

        try:
            with Image.open(sample_path) as img, Image.open(wm_path) as wm:
                composed = apply_watermark(img, wm, cfg)
                pix = pil_to_qpixmap(composed)
                pix = pix.scaled(self.preview_label.width() - 8, self.preview_label.height() - 8, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                self.preview_label.setPixmap(pix)
        except Exception as e:
            self.preview_label.setText(f"预览失败: {type(e).__name__}: {e}")

    def _build_export_tab(self) -> None:
        layout = QVBoxLayout(self.export_tab)

        folder_box = QGroupBox("导出设置")
        f = QFormLayout(folder_box)
        self.second_line = QLineEdit(str(self.second_root))
        btn1 = QPushButton("选择商品图分组目录")
        btn1.clicked.connect(lambda: self._pick_dir(self.second_line))
        row1 = QHBoxLayout()
        self._normalize_row_spacing(row1)
        row1.addWidget(self.second_line)
        row1.addWidget(btn1)
        w1 = QWidget()
        w1.setLayout(row1)
        f.addRow("商品图分组目录", w1)

        self.export_line = QLineEdit(str(self.export_root))
        btn2 = QPushButton("选择导出目录")
        btn2.clicked.connect(lambda: self._pick_dir(self.export_line))
        row2 = QHBoxLayout()
        self._normalize_row_spacing(row2)
        row2.addWidget(self.export_line)
        row2.addWidget(btn2)
        w2 = QWidget()
        w2.setLayout(row2)
        f.addRow("出图目录", w2)

        self.export_config_selector = QComboBox()
        f.addRow("水印配置", self.export_config_selector)
        layout.addWidget(folder_box)

        btn_row = QHBoxLayout()
        self._normalize_row_spacing(btn_row)
        refresh_btn = QPushButton("刷新文件夹与配置")
        refresh_btn.clicked.connect(self.refresh_export_folder_list)
        run_btn = QPushButton("按配置批量加水印并出图")
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
        current = self.export_config_selector.currentText()
        self.export_config_selector.clear()
        for p in sorted(self.config_root.glob("*.json")):
            self.export_config_selector.addItem(p.stem)
        if current:
            idx = self.export_config_selector.findText(current)
            if idx >= 0:
                self.export_config_selector.setCurrentIndex(idx)

    def refresh_export_folder_list(self) -> None:
        self.second_folder_list.clear()
        second_root = Path(self.second_line.text().strip()) if hasattr(self, "second_line") else self.second_root
        if not second_root.exists():
            return
        for d in sorted(second_root.iterdir()):
            if not d.is_dir():
                continue
            cnt = len([p for p in d.iterdir() if is_image_file(p)])
            item = QListWidgetItem(f"{d.name} ({cnt} 张)")
            item.setData(Qt.UserRole, str(d))
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Checked)
            self.second_folder_list.addItem(item)

    def batch_export(self) -> None:
        cfg_name = self.export_config_selector.currentText().strip()
        if not cfg_name:
            QMessageBox.warning(self, "错误", "请先选择水印配置")
            return
        cfg_data = load_json(self.config_root / f"{cfg_name}.json", None)
        if not cfg_data:
            QMessageBox.warning(self, "错误", "配置文件读取失败")
            return
        cfg = WatermarkConfig(**cfg_data)

        wm_path = self.watermark_root / cfg.watermark_file
        if not wm_path.exists():
            QMessageBox.warning(self, "错误", f"水印文件不存在: {wm_path.name}")
            return

        second_root = Path(self.second_line.text().strip())
        export_root = Path(self.export_line.text().strip())
        ensure_dir(export_root)

        selected_dirs: List[Path] = []
        for i in range(self.second_folder_list.count()):
            it = self.second_folder_list.item(i)
            if it.checkState() == Qt.Checked:
                selected_dirs.append(Path(it.data(Qt.UserRole)))
        if not selected_dirs:
            QMessageBox.warning(self, "提示", "请至少勾选一个二层文件夹")
            return

        ok_folder = 0
        ok_img = 0
        with Image.open(wm_path) as wm:
            for src_dir in selected_dirs:
                imgs = [p for p in sorted(src_dir.iterdir()) if is_image_file(p)]
                if not (16 <= len(imgs) <= 20):
                    self._log(self.export_log, f"跳过 {src_dir.name}: 数量 {len(imgs)} 不在 16-20")
                    continue

                out_dir = export_root / src_dir.name
                ensure_dir(out_dir)
                one_folder_ok = 0
                for idx, img_path in enumerate(imgs, 1):
                    try:
                        with Image.open(img_path) as im:
                            out = apply_watermark(im, wm, cfg)
                        out_name = f"{src_dir.name}_{idx:03d}.jpg"
                        out.save(out_dir / out_name, "JPEG", quality=95, optimize=True)
                        one_folder_ok += 1
                        ok_img += 1
                    except Exception as e:
                        self._log(self.export_log, f"失败 {img_path.name}: {type(e).__name__}")

                if 16 <= one_folder_ok <= 20:
                    ok_folder += 1
                    self._log(self.export_log, f"{src_dir.name}: 出图 {one_folder_ok} 张")
                else:
                    self._log(self.export_log, f"{src_dir.name}: 警告，成功 {one_folder_ok} 张，不满足16-20")

        self._log(self.export_log, f"批量完成: 合规文件夹 {ok_folder}，输出图片 {ok_img}")


def main() -> None:
    app = QApplication(sys.argv)
    w = MainWindow()
    w.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
