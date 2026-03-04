from dataclasses import asdict
from pathlib import Path
import shutil
from typing import Callable, Optional, Type

from PIL import Image
from PyQt5.QtWidgets import QMessageBox


def _trim_transparent_edges(png_path: Path) -> None:
    with Image.open(png_path) as im:
        rgba = im.convert("RGBA")
        bbox = rgba.getchannel("A").getbbox()
        if not bbox:
            return
        rgba.crop(bbox).save(png_path, format="PNG")


def add_watermark_file(ctx) -> None:
    src = Path(ctx.watermark_file_line.text().strip())
    if not src.exists() or src.suffix.lower() != ".png":
        QMessageBox.warning(ctx, "错误", "请选择有效的 PNG 水印文件")
        return

    custom_name = ctx.watermark_name_line.text().strip()
    if custom_name:
        invalid = set('<>:"/\\|?*')
        if any(ch in invalid for ch in custom_name):
            QMessageBox.warning(ctx, "错误", "水印名称包含非法字符")
            return
        dst_name = f"{custom_name}.png"
    else:
        dst_name = src.name

    dst = ctx.watermark_root / dst_name
    if dst.exists():
        stem = dst.stem
        for i in range(2, 10000):
            candidate = ctx.watermark_root / f"{stem}_{i}.png"
            if not candidate.exists():
                dst = candidate
                break

    shutil.copy2(src, dst)
    _trim_transparent_edges(dst)
    ctx._log(ctx.wm_log, f"已入库水印: {dst.name}")
    ctx.watermark_name_line.clear()
    ctx.refresh_watermark_lists()


def apply_wm_settings(ctx) -> None:
    wm_file = ctx._selected_wm_filename()
    if not wm_file:
        QMessageBox.information(ctx, "提示", "请先选择水印")
        return

    wm_path = ctx.watermark_root / wm_file
    if not wm_path.exists():
        QMessageBox.warning(ctx, "错误", "水印文件不存在")
        return

    try:
        with Image.open(wm_path) as wm_raw:
            wm_processed = ctx._apply_watermark_image_settings(wm_raw)
        wm_processed.save(wm_path, format="PNG")
        _reset_wm_caches(ctx)
        ctx._wm_current_border_norm = None
        ctx._fx_current_border_norm = None
        ctx._reset_wm_setting_sliders()
        ctx.refresh_watermark_lists()
        ctx.update_preview()
        ctx._log(ctx.wm_log, f"已应用并覆盖水印: {wm_path.name}")
    except Exception as e:
        QMessageBox.critical(ctx, "应用失败", f"{type(e).__name__}: {e}")


def reset_wm_settings(ctx) -> None:
    ctx._reset_wm_setting_sliders()
    ctx._wm_current_border_norm = None
    ctx._fx_current_border_norm = None
    ctx.update_preview()
    ctx._log(ctx.wm_log, "已重置到当前水印最后一次应用状态")


def delete_selected_watermark(ctx) -> None:
    wm_file = ctx._selected_wm_filename()
    if not wm_file:
        QMessageBox.information(ctx, "提示", "请先选择水印")
        return

    wm_path = ctx.watermark_root / wm_file
    if not wm_path.exists():
        QMessageBox.warning(ctx, "错误", "水印文件不存在")
        return

    ans = QMessageBox.question(
        ctx,
        "删除水印",
        f"确认删除水印“{wm_path.stem}”吗？",
        QMessageBox.Yes | QMessageBox.No,
        QMessageBox.No,
    )
    if ans != QMessageBox.Yes:
        return

    try:
        wm_path.unlink()
        _reset_wm_caches(ctx)
        ctx._log(ctx.wm_log, f"已删除水印: {wm_path.name}")
        ctx.refresh_watermark_lists()
        ctx.refresh_export_config_list()
        ctx.update_preview()
    except Exception as e:
        QMessageBox.critical(ctx, "删除失败", f"{type(e).__name__}: {e}")


def refresh_watermark_lists(ctx) -> None:
    current_wm = ctx._selected_wm_filename()
    ctx.wm_selector.clear()
    for p in sorted(ctx.watermark_root.glob("*.png")):
        ctx.wm_selector.addItem(ctx._make_wm_selector_icon(p), p.stem, p.name)

    if current_wm:
        for i in range(ctx.wm_selector.count()):
            if ctx.wm_selector.itemData(i) == current_wm:
                ctx.wm_selector.setCurrentIndex(i)
                break

    current_cfg = ctx.config_selector.currentText() if hasattr(ctx, "config_selector") else ""
    if hasattr(ctx, "config_selector"):
        ctx.config_selector.clear()
        for p in sorted(ctx.config_root.glob("*.json")):
            ctx.config_selector.addItem(p.stem)
        if current_cfg:
            idx = ctx.config_selector.findText(current_cfg)
            if idx >= 0:
                ctx.config_selector.setCurrentIndex(idx)


def current_cfg_obj(ctx, watermark_config_cls: Type) -> Optional[object]:
    wm_name = ctx._selected_wm_filename()
    cfg_name = ctx.cfg_name.text().strip()
    if not wm_name or not cfg_name:
        return None

    return watermark_config_cls(
        name=cfg_name,
        watermark_file=wm_name,
        x_pct=ctx.final_x_slider.value(),
        y_pct=ctx.final_y_slider.value(),
        scale_pct=ctx.final_scale_slider.value(),
        rotate_deg=ctx.final_rotate_slider.value(),
        opacity_pct=ctx.final_opacity_slider.value(),
    )


def save_current_config(ctx, *, save_json: Callable[[Path, object], None], watermark_config_cls: Type) -> None:
    cfg = current_cfg_obj(ctx, watermark_config_cls)
    if not cfg:
        QMessageBox.warning(ctx, "错误", "请先选择水印并填写配置名")
        return

    save_json(ctx.config_root / f"{cfg.name}.json", asdict(cfg))
    ctx._log(ctx.wm_log, f"配置已保存: {cfg.name}")
    ctx.refresh_watermark_lists()
    ctx.refresh_export_config_list()


def load_selected_config(ctx, *, load_json: Callable[[Path, object], object]) -> None:
    name = ctx.config_selector.currentText().strip()
    if not name:
        return

    data = load_json(ctx.config_root / f"{name}.json", None)
    if not data:
        return

    ctx.cfg_name.setText(data.get("name", name))
    wm_file = data.get("watermark_file", "")
    for i in range(ctx.wm_selector.count()):
        if ctx.wm_selector.itemData(i) == wm_file:
            ctx.wm_selector.setCurrentIndex(i)
            break

    ctx.final_x_slider.setValue(int(data.get("x_pct", 50)))
    ctx.final_y_slider.setValue(int(data.get("y_pct", 50)))
    ctx.final_scale_slider.setValue(int(data.get("scale_pct", 25)))
    ctx.final_rotate_slider.setValue(int(data.get("rotate_deg", 0)))
    ctx.final_opacity_slider.setValue(int(data.get("opacity_pct", 100)))
    ctx.update_preview()


def _reset_wm_caches(ctx) -> None:
    ctx._wm_raw_cache_key = None
    ctx._wm_raw_cache_img = None
    ctx._wm_sim_cache_key = None
    ctx._wm_sim_cache_img = None
    ctx._wm_preview_pix_cache_key = None
    ctx._wm_preview_pix_cache_img = None
    if hasattr(ctx, "_wm_preview_pix_cache_bbox_norm"):
        ctx._wm_preview_pix_cache_bbox_norm = None
