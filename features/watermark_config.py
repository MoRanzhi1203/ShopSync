from dataclasses import asdict
from pathlib import Path
import shutil
from typing import Optional, Type

from PIL import Image
from PyQt5.QtWidgets import QMessageBox

from features.utils import load_json, save_json


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
    if hasattr(ctx, "_save_applied_wm_settings_for_file"):
        ctx._save_applied_wm_settings_for_file(dst.name, 0, 100)
    ctx._log(ctx.wm_log, f"已入库水印: {dst.name}")
    ctx.watermark_name_line.clear()
    ctx.refresh_watermark_lists()
    for i in range(ctx.wm_selector.count()):
        if ctx.wm_selector.itemData(i) == dst.name:
            ctx.wm_selector.setCurrentIndex(i)
            break


def apply_wm_settings(ctx) -> None:
    wm_file = ctx._selected_wm_filename()
    if not wm_file:
        QMessageBox.information(ctx, "提示", "请先选择水印")
        return

    try:
        applied_rotate = int(ctx._effective_wm_rotate_for_file(wm_file))
        applied_opacity = int(ctx._effective_wm_opacity_for_file(wm_file))
        if hasattr(ctx, "_save_applied_wm_settings_for_file"):
            ctx._save_applied_wm_settings_for_file(wm_file, applied_rotate, applied_opacity)
        _reset_wm_caches(ctx)
        ctx._wm_current_border_norm = None
        ctx._fx_current_border_norm = None
        ctx._reset_wm_setting_sliders()
        # Keep opacity at the just-applied value; only rotation delta returns to 0.
        if hasattr(ctx, "wm_opacity_slider"):
            ctx.wm_opacity_slider.setValue(applied_opacity)
        if hasattr(ctx, "_save_wm_settings_for_file"):
            ctx._save_wm_settings_for_file(wm_file)
        # Refresh selector icons so applied rotation is reflected immediately.
        ctx.refresh_watermark_lists()
        ctx.update_preview()
        ctx._log(ctx.wm_log, f"已应用水印设置(基于母版): {wm_file}")
    except Exception as e:
        QMessageBox.critical(ctx, "应用失败", f"{type(e).__name__}: {e}")


def reset_wm_settings(ctx) -> None:
    wm_file = ctx._selected_wm_filename()
    if hasattr(ctx, "_reset_wm_settings_to_last_applied"):
        ctx._reset_wm_settings_to_last_applied(wm_file)
    else:
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
        if hasattr(ctx, "_wm_applied_settings_by_file"):
            ctx._wm_applied_settings_by_file.pop(wm_file, None)
            if hasattr(ctx, "_persist_wm_applied_settings"):
                ctx._persist_wm_applied_settings()
        if hasattr(ctx, "_wm_settings_by_file"):
            ctx._wm_settings_by_file.pop(wm_file, None)
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
        ctx.config_selector.blockSignals(True)
        ctx.config_selector.clear()
        for p in sorted(ctx.config_root.glob("*.json")):
            ctx.config_selector.addItem(p.stem)
        if current_cfg:
            idx = ctx.config_selector.findText(current_cfg)
            if idx >= 0:
                ctx.config_selector.setCurrentIndex(idx)
            else:
                ctx.config_selector.setCurrentIndex(-1)
        else:
            # Do not auto-select first config on refresh; keep "default checked" behavior intact.
            ctx.config_selector.setCurrentIndex(-1)
        ctx.config_selector.blockSignals(False)
        if not current_cfg and hasattr(ctx, "final_constrain_inside_checkbox"):
            ctx.final_constrain_inside_checkbox.setChecked(True)

    if hasattr(ctx, "group_wm_selector"):
        current_group_wm = ctx.group_wm_selector.currentData()
        if not isinstance(current_group_wm, str) or not current_group_wm:
            current_group_wm = ctx.group_wm_selector.currentText().strip()
        ctx.group_wm_selector.blockSignals(True)
        ctx.group_wm_selector.clear()
        for p in sorted(ctx.watermark_root.glob("*.png")):
            ctx.group_wm_selector.addItem(p.stem, p.name)
        if current_group_wm:
            idx = -1
            for i in range(ctx.group_wm_selector.count()):
                if ctx.group_wm_selector.itemData(i) == current_group_wm:
                    idx = i
                    break
            if idx < 0:
                idx = ctx.group_wm_selector.findText(str(current_group_wm))
            if idx >= 0:
                ctx.group_wm_selector.setCurrentIndex(idx)
        ctx.group_wm_selector.blockSignals(False)

    if hasattr(ctx, "group_cfg_selector"):
        current_group_cfg = ctx.group_cfg_selector.currentText().strip()
        ctx.group_cfg_selector.blockSignals(True)
        ctx.group_cfg_selector.clear()
        for p in sorted(ctx.config_root.glob("*.json")):
            ctx.group_cfg_selector.addItem(p.stem)
        if current_group_cfg:
            idx = ctx.group_cfg_selector.findText(current_group_cfg)
            if idx >= 0:
                ctx.group_cfg_selector.setCurrentIndex(idx)
        ctx.group_cfg_selector.blockSignals(False)


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
        constrain_inside=bool(ctx.final_constrain_inside_checkbox.isChecked())
        if hasattr(ctx, "final_constrain_inside_checkbox")
        else True,
    )


def save_current_config(ctx, *, watermark_config_cls: Type) -> None:
    cfg = current_cfg_obj(ctx, watermark_config_cls)
    if not cfg:
        QMessageBox.warning(ctx, "错误", "请先选择水印并填写配置名")
        return

    save_json(ctx.config_root / f"{cfg.name}.json", asdict(cfg))
    ctx._log(ctx.wm_log, f"配置已保存: {cfg.name}")
    ctx.refresh_watermark_lists()
    if hasattr(ctx, "config_selector"):
        idx = ctx.config_selector.findText(cfg.name)
        if idx >= 0:
            ctx.config_selector.setCurrentIndex(idx)
    ctx.refresh_export_config_list()


def load_selected_config(ctx) -> None:
    name = ctx.config_selector.currentText().strip()
    if not name:
        if hasattr(ctx, "final_constrain_inside_checkbox"):
            ctx.final_constrain_inside_checkbox.setChecked(True)
        return

    data = load_json(ctx.config_root / f"{name}.json", None)
    if not data:
        return

    ctx.cfg_name.setText(data.get("name", name))
    # Keep watermark selection unchanged when switching config.

    ctx.final_x_slider.setValue(int(data.get("x_pct", 50)))
    ctx.final_y_slider.setValue(int(data.get("y_pct", 50)))
    ctx.final_scale_slider.setValue(int(data.get("scale_pct", 25)))
    ctx.final_rotate_slider.setValue(int(data.get("rotate_deg", 0)))
    ctx.final_opacity_slider.setValue(int(data.get("opacity_pct", 100)))
    if hasattr(ctx, "final_constrain_inside_checkbox"):
        ctx.final_constrain_inside_checkbox.setChecked(bool(data.get("constrain_inside", True)))
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
