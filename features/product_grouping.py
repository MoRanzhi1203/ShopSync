import random
from pathlib import Path
from typing import Callable, List, Optional, Type

from PIL import Image
from PyQt5.QtWidgets import QListWidgetItem, QMessageBox


def _group_size_bounds(ctx) -> tuple[int, int]:
    if hasattr(ctx, "per_group_min") and hasattr(ctx, "per_group_max"):
        mn = int(ctx.per_group_min.value())
        mx = int(ctx.per_group_max.value())
    else:
        v = int(ctx.per_group.value())
        mn, mx = v, v
    if mn > mx:
        mn, mx = mx, mn
    mn = max(4, min(32, mn))
    mx = max(4, min(32, mx))
    return mn, mx


def _plan_group_sizes(group_count: int, min_size: int, max_size: int) -> List[int]:
    if group_count <= 0:
        return []
    if min_size == max_size:
        return [min_size] * group_count
    return [random.randint(min_size, max_size) for _ in range(group_count)]


def pick_candidates(ctx, *, list_images: Callable[[Path], List[Path]]) -> None:
    _ = ctx, list_images
    return


def resolve_candidate_item_path(ctx, item: QListWidgetItem) -> Optional[Path]:
    _ = ctx, item
    return None


def build_second_layer(
    ctx,
    *,
    list_images: Callable[[Path], List[Path]],
    load_json: Callable[[Path, object], object],
    apply_watermark: Callable[[Image.Image, Image.Image, object], Image.Image],
    ensure_dir: Callable[[Path], None],
    next_folder_index: Callable[[Path, str], int],
    watermark_config_cls: Type,
) -> None:
    wm_stem = ctx.group_wm_selector.currentText().strip() if hasattr(ctx, "group_wm_selector") else ""
    cfg_name = ctx.group_cfg_selector.currentText().strip() if hasattr(ctx, "group_cfg_selector") else ""
    if not wm_stem:
        QMessageBox.warning(ctx, "提示", "请先选择水印")
        return
    if not cfg_name:
        QMessageBox.warning(ctx, "提示", "请先选择配置")
        return

    wm_file = ""
    for p in sorted(ctx.watermark_root.glob("*.png")):
        if p.stem == wm_stem:
            wm_file = p.name
            break
    if not wm_file:
        QMessageBox.warning(ctx, "错误", f"未找到水印文件: {wm_stem}")
        return
    wm_path = ctx.watermark_root / wm_file
    if not wm_path.exists():
        QMessageBox.warning(ctx, "错误", f"水印文件不存在: {wm_file}")
        return

    cfg_data = load_json(ctx.config_root / f"{cfg_name}.json", None)
    if not isinstance(cfg_data, dict):
        QMessageBox.warning(ctx, "错误", f"配置读取失败: {cfg_name}")
        return
    cfg_data["watermark_file"] = wm_file
    cfg = watermark_config_cls(**cfg_data)
    if hasattr(ctx, "_get_applied_wm_rotate"):
        setattr(cfg, "wm_base_rotate", int(ctx._get_applied_wm_rotate(wm_file)))
    if hasattr(ctx, "_get_applied_wm_opacity"):
        setattr(cfg, "wm_base_opacity", int(ctx._get_applied_wm_opacity(wm_file)))

    all_images = list_images(ctx.internal_root)
    min_size, max_size = _group_size_bounds(ctx)
    plan_sizes = _plan_group_sizes(ctx.group_count.value(), min_size, max_size)
    need = sum(plan_sizes)
    if len(all_images) < need:
        QMessageBox.warning(ctx, "提示", f"内部库图片不足。需要 {need}，当前 {len(all_images)}")
        return

    selected = random.sample(all_images, need)
    random.shuffle(selected)

    prefix = f"{wm_stem}_{cfg_name}_"
    base_idx = next_folder_index(ctx.second_root, prefix)
    cursor = 0
    created = 0
    with Image.open(wm_path) as wm_img:
        for g in range(ctx.group_count.value()):
            folder_name = f"{prefix}{base_idx + g:03d}"
            out_dir = ctx.second_root / folder_name
            ensure_dir(out_dir)
            created += 1

            per_count = plan_sizes[g]
            ok = 0
            for i in range(per_count):
                src = selected[cursor]
                cursor += 1
                try:
                    with Image.open(src) as im:
                        out = apply_watermark(im, wm_img, cfg)
                    dst = out_dir / f"{folder_name}_{i + 1:03d}.jpg"
                    out.save(dst, "JPEG", quality=95, optimize=True)
                    ok += 1
                except Exception as e:
                    ctx._log(ctx.group_log, f"{folder_name}: 跳过 {src.name} ({type(e).__name__})")
            ctx._log(ctx.group_log, f"{folder_name}: {ok}/{per_count} 张")

    if min_size == max_size:
        ctx._log(ctx.group_log, f"完成: 生成二层文件夹 {created} 个，均为 {min_size} 张（已按所选水印+配置合成）")
    else:
        ctx._log(ctx.group_log, f"完成: 生成二层文件夹 {created} 个，每组范围 {min_size}-{max_size} 张（已按所选水印+配置合成）")
    ctx.refresh_export_folder_list()
