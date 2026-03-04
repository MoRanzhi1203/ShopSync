from pathlib import Path
from typing import Callable, List, Type

from PIL import Image
from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QListWidgetItem, QMessageBox


def refresh_export_config_list(ctx) -> None:
    current = ctx.export_config_selector.currentText()
    ctx.export_config_selector.clear()
    for p in sorted(ctx.config_root.glob("*.json")):
        ctx.export_config_selector.addItem(p.stem)
    if current:
        idx = ctx.export_config_selector.findText(current)
        if idx >= 0:
            ctx.export_config_selector.setCurrentIndex(idx)


def refresh_export_folder_list(ctx, *, is_image_file: Callable[[Path], bool]) -> None:
    ctx.second_folder_list.clear()
    second_root = Path(ctx.second_line.text().strip()) if hasattr(ctx, "second_line") else ctx.second_root
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
        ctx.second_folder_list.addItem(item)


def batch_export(
    ctx,
    *,
    load_json: Callable[[Path, object], object],
    apply_watermark: Callable[[Image.Image, Image.Image, object], Image.Image],
    ensure_dir: Callable[[Path], None],
    is_image_file: Callable[[Path], bool],
    watermark_config_cls: Type,
) -> None:
    cfg_name = ctx.export_config_selector.currentText().strip()
    if not cfg_name:
        QMessageBox.warning(ctx, "错误", "请先选择水印配置")
        return
    cfg_data = load_json(ctx.config_root / f"{cfg_name}.json", None)
    if not cfg_data:
        QMessageBox.warning(ctx, "错误", "配置文件读取失败")
        return
    cfg = watermark_config_cls(**cfg_data)

    wm_path = ctx.watermark_root / cfg.watermark_file
    if not wm_path.exists():
        QMessageBox.warning(ctx, "错误", f"水印文件不存在: {wm_path.name}")
        return

    export_root = Path(ctx.export_line.text().strip())
    ensure_dir(export_root)

    selected_dirs: List[Path] = []
    for i in range(ctx.second_folder_list.count()):
        it = ctx.second_folder_list.item(i)
        if it.checkState() == Qt.Checked:
            selected_dirs.append(Path(it.data(Qt.UserRole)))
    if not selected_dirs:
        QMessageBox.warning(ctx, "提示", "请至少勾选一个二层文件夹")
        return

    ok_folder = 0
    ok_img = 0
    with Image.open(wm_path) as wm:
        for src_dir in selected_dirs:
            imgs = [p for p in sorted(src_dir.iterdir()) if is_image_file(p)]
            if not (16 <= len(imgs) <= 20):
                ctx._log(ctx.export_log, f"跳过 {src_dir.name}: 数量 {len(imgs)} 不在 16-20")
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
                    ctx._log(ctx.export_log, f"失败 {img_path.name}: {type(e).__name__}")

            if 16 <= one_folder_ok <= 20:
                ok_folder += 1
                ctx._log(ctx.export_log, f"{src_dir.name}: 出图 {one_folder_ok} 张")
            else:
                ctx._log(ctx.export_log, f"{src_dir.name}: 警告，成功 {one_folder_ok} 张，不满足 16-20")

    ctx._log(ctx.export_log, f"批量完成: 合规文件夹 {ok_folder}，输出图片 {ok_img}")

