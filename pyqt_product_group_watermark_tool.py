# -*- coding: utf-8 -*-
import hashlib
import json
import random
import shutil
import sys
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from PIL import Image
from PyQt5.QtCore import Qt
from PyQt5.QtGui import QImage, QPixmap
from PyQt5.QtWidgets import (
    QApplication,
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
    QSpinBox,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
    QComboBox,
    QSlider,
)


IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff"}


@dataclass
class WatermarkConfig:
    name: str
    watermark_file: str
    x_pct: int = 50
    y_pct: int = 50
    scale_pct: int = 25
    rotate_deg: int = 0
    opacity_pct: int = 100


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

        for p in [self.internal_root, self.second_root, self.export_root, self.watermark_root, self.config_root]:
            ensure_dir(p)

        self.md5_index: Dict[str, str] = load_json(self.index_path, {})
        self.candidate_images: List[Path] = []

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

    def _pick_dir(self, line: QLineEdit) -> None:
        d = QFileDialog.getExistingDirectory(self, "选择目录", str(self.root))
        if d:
            line.setText(d)

    def _pick_file(self, line: QLineEdit, flt: str) -> None:
        p, _ = QFileDialog.getOpenFileName(self, "选择文件", str(self.root), flt)
        if p:
            line.setText(p)

    def _build_import_tab(self) -> None:
        layout = QVBoxLayout(self.import_tab)

        form = QFormLayout()
        self.src_import_line = QLineEdit(str(self.root / "input_images"))
        pick_btn = QPushButton("选择目录")
        pick_btn.clicked.connect(lambda: self._pick_dir(self.src_import_line))
        row = QHBoxLayout()
        row.addWidget(self.src_import_line)
        row.addWidget(pick_btn)
        w = QWidget()
        w.setLayout(row)
        form.addRow("待导入根目录", w)
        layout.addLayout(form)

        btn = QPushButton("开始导入并去重")
        btn.clicked.connect(self.import_to_internal_library)
        layout.addWidget(btn)

        split = QHBoxLayout()
        left_col = QVBoxLayout()
        left_col.addWidget(QLabel("当前分组"))
        self.internal_group_list = QListWidget()
        left_col.addWidget(self.internal_group_list)
        left_wrap = QWidget()
        left_wrap.setLayout(left_col)
        split.addWidget(left_wrap, 1)

        right_col = QVBoxLayout()
        right_col.addWidget(QLabel("日志输出"))
        self.import_log = QTextEdit()
        self.import_log.setReadOnly(True)
        right_col.addWidget(self.import_log)
        right_wrap = QWidget()
        right_wrap.setLayout(right_col)
        split.addWidget(right_wrap, 2)
        layout.addLayout(split)

        self.refresh_internal_group_list()

    def refresh_internal_group_list(self) -> None:
        self.internal_group_list.clear()
        if not self.internal_root.exists():
            return
        for d in sorted(self.internal_root.iterdir()):
            if not d.is_dir():
                continue
            cnt = len([p for p in d.iterdir() if is_image_file(p)])
            self.internal_group_list.addItem(f"{d.name} ({cnt} 张)")

    def import_to_internal_library(self) -> None:
        src_root = Path(self.src_import_line.text().strip())
        if not src_root.exists():
            QMessageBox.warning(self, "错误", "导入目录不存在")
            return

        imported = 0
        skipped_dup = 0
        folders_created = 0

        children = [p for p in sorted(src_root.iterdir()) if p.is_dir()]
        if not children:
            QMessageBox.warning(self, "提示", "导入目录下没有子文件夹")
            return

        for source_folder in children:
            base_name = source_folder.name.strip()
            if not base_name:
                continue
            images = list_images(source_folder)
            if not images:
                self._log(self.import_log, f"{source_folder.name}: 未发现图片")
                continue

            new_items: List[Tuple[Path, str]] = []
            for img in images:
                h = md5_of_file(img)
                if h in self.md5_index:
                    skipped_dup += 1
                    continue
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
                ext = src_img.suffix.lower()
                dst_name = f"{base_name}{next_idx:03d}{ext}"
                next_idx += 1
                dst = dst_folder / dst_name
                shutil.copy2(src_img, dst)
                self.md5_index[h] = str(dst.relative_to(self.root))
                imported += 1

            final_count = len([p for p in dst_folder.iterdir() if is_image_file(p)])
            if final_count < 50:
                self._log(self.import_log, f"{base_name}: 当前总数 {final_count} (<50)，建议后续补图")
            else:
                self._log(self.import_log, f"{base_name}: 新增 {len(new_items)} 张，当前总数 {final_count} 张")

        save_json(self.index_path, self.md5_index)
        self._log(self.import_log, f"完成: 新增 {imported}，重复跳过 {skipped_dup}，新建内部库文件夹 {folders_created}")
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
        self.sample_image_line = QLineEdit()
        pick_sample = QPushButton("选择预览底图")
        pick_sample.clicked.connect(lambda: self._pick_file(self.sample_image_line, "Image Files (*.png *.jpg *.jpeg *.webp *.bmp)"))
        sample_row.addWidget(self.sample_image_line)
        sample_row.addWidget(pick_sample)
        sample_w = QWidget()
        sample_w.setLayout(sample_row)
        layout.addWidget(sample_w)

        btn_row = QHBoxLayout()
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
        row1.addWidget(self.second_line)
        row1.addWidget(btn1)
        w1 = QWidget()
        w1.setLayout(row1)
        f.addRow("商品图分组目录", w1)

        self.export_line = QLineEdit(str(self.export_root))
        btn2 = QPushButton("选择导出目录")
        btn2.clicked.connect(lambda: self._pick_dir(self.export_line))
        row2 = QHBoxLayout()
        row2.addWidget(self.export_line)
        row2.addWidget(btn2)
        w2 = QWidget()
        w2.setLayout(row2)
        f.addRow("出图目录", w2)

        self.export_config_selector = QComboBox()
        f.addRow("水印配置", self.export_config_selector)
        layout.addWidget(folder_box)

        btn_row = QHBoxLayout()
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
