import random
import shutil
import time
from pathlib import Path
from typing import Callable, List, Optional, Type

from PIL import Image
from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QApplication, QListWidgetItem, QMessageBox, QProgressDialog


def pick_candidates(ctx, *, list_images: Callable[[Path], List[Path]]) -> None:
    _ = ctx, list_images
    return


def resolve_candidate_item_path(ctx, item: QListWidgetItem) -> Optional[Path]:
    _ = ctx, item
    return None


def _ordered_color_group_dirs(ctx) -> List[Path]:
    out: List[Path] = []
    if hasattr(ctx, "internal_group_list") and ctx.internal_group_list.count() > 0:
        for i in range(ctx.internal_group_list.count()):
            it = ctx.internal_group_list.item(i)
            if it is None:
                continue
            raw = it.data(Qt.UserRole)
            if not raw:
                continue
            d = Path(str(raw))
            if d.exists() and d.is_dir():
                out.append(d)
        if out:
            return out
    if hasattr(ctx, "_get_ordered_internal_group_dirs"):
        try:
            return [d for d in ctx._get_ordered_internal_group_dirs() if d.exists() and d.is_dir()]
        except Exception:
            pass
    return []


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
    def _wait_if_paused() -> bool:
        while bool(getattr(ctx, "_group_paused", False)):
            if getattr(ctx, "_group_stop_mode", None) in {"stop", "cancel"}:
                return True
            progress.setLabelText(f"已暂停\n总进度 {done}/{total_tasks}")
            QApplication.processEvents()
            time.sleep(0.05)
        return False

    wm_data = ctx.group_wm_selector.currentData() if hasattr(ctx, "group_wm_selector") else ""
    wm_stem = ctx.group_wm_selector.currentText().strip() if hasattr(ctx, "group_wm_selector") else ""
    cfg_name = ctx.group_cfg_selector.currentText().strip() if hasattr(ctx, "group_cfg_selector") else ""
    if not wm_stem:
        QMessageBox.warning(ctx, "提示", "请先选择水印")
        return
    if not cfg_name:
        QMessageBox.warning(ctx, "提示", "请先选择配置")
        return

    wm_file = ""
    if isinstance(wm_data, str) and wm_data.strip():
        wm_file = wm_data.strip()
    if not wm_file:
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
    if hasattr(ctx, "_effective_wm_rotate_for_file"):
        setattr(cfg, "wm_base_rotate", int(ctx._effective_wm_rotate_for_file(wm_file)))
    elif hasattr(ctx, "_get_applied_wm_rotate"):
        setattr(cfg, "wm_base_rotate", int(ctx._get_applied_wm_rotate(wm_file)))
    if hasattr(ctx, "_effective_wm_opacity_for_file"):
        setattr(cfg, "wm_base_opacity", int(ctx._effective_wm_opacity_for_file(wm_file)))
    elif hasattr(ctx, "_get_applied_wm_opacity"):
        setattr(cfg, "wm_base_opacity", int(ctx._get_applied_wm_opacity(wm_file)))

    color_group_dirs = _ordered_color_group_dirs(ctx)
    if not color_group_dirs:
        QMessageBox.warning(ctx, "提示", "颜色分组为空")
        return

    group_image_pool: List[tuple[Path, List[Path]]] = []
    for d in color_group_dirs:
        imgs = [p for p in sorted(d.iterdir()) if p.is_file() and p.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff"}]
        if not imgs:
            QMessageBox.warning(ctx, "提示", f"颜色分组为空: {d.name}")
            return
        group_image_pool.append((d, imgs))

    folder_count = int(ctx.group_count.value()) if hasattr(ctx, "group_count") else 1
    folder_count = max(1, folder_count)
    total_groups = len(group_image_pool)
    total_tasks = max(1, folder_count * total_groups)
    cache_root = Path(ctx.second_line.text().strip()) if hasattr(ctx, "second_line") else ctx.second_root
    ensure_dir(cache_root)
    prefix = f"{wm_stem}_{cfg_name}_"
    base_idx = next_folder_index(cache_root, prefix)
    created = 0
    done = 0
    stop_mode = ""
    created_dirs: List[Path] = []

    progress = QProgressDialog("正在随机抽取并合成...", "", 0, total_tasks, ctx)
    progress.setWindowTitle("处理中")
    progress.setCancelButton(None)
    progress.setWindowModality(Qt.NonModal)
    progress.setMinimumDuration(0)
    progress.setAutoClose(False)
    progress.setAutoReset(False)
    progress.show()
    if hasattr(ctx, "_set_group_action_buttons"):
        ctx._set_group_action_buttons(running=True)
    QApplication.processEvents()

    try:
        with Image.open(wm_path) as wm_img:
            for i in range(folder_count):
                if _wait_if_paused():
                    stop_mode = str(getattr(ctx, "_group_stop_mode", ""))
                    break
                if getattr(ctx, "_group_stop_mode", None) in {"stop", "cancel"}:
                    stop_mode = str(getattr(ctx, "_group_stop_mode", ""))
                    break
                folder_name = f"{prefix}{base_idx + i:04d}"
                out_dir = cache_root / folder_name
                ensure_dir(out_dir)
                created_dirs.append(out_dir)
                created += 1
                ok = 0
                total = len(group_image_pool)
                for seq, (_gdir, pool) in enumerate(group_image_pool, 1):
                    if _wait_if_paused():
                        stop_mode = str(getattr(ctx, "_group_stop_mode", ""))
                        break
                    if getattr(ctx, "_group_stop_mode", None) in {"stop", "cancel"}:
                        stop_mode = str(getattr(ctx, "_group_stop_mode", ""))
                        break
                    src = random.choice(pool)
                    done += 1
                    progress.setValue(done)
                    progress.setLabelText(f"正在合成 {folder_name} ({seq}/{total})\n总进度 {done}/{total_tasks}")
                    QApplication.processEvents()
                    try:
                        with Image.open(src) as im:
                            out = apply_watermark(im, wm_img, cfg)
                        dst = out_dir / f"{folder_name}{seq:03d}.jpg"
                        out.save(dst, "JPEG", quality=95, optimize=True)
                        ok += 1
                    except Exception as e:
                        ctx._log(ctx.group_log, f"{folder_name}: 跳过 {src.name} ({type(e).__name__})")
                ctx._log(ctx.group_log, f"{folder_name}: {ok}/{total} 张")
                ctx.refresh_export_folder_list()
                QApplication.processEvents()
                if stop_mode:
                    break
        if not stop_mode and getattr(ctx, "_group_stop_mode", None) in {"stop", "cancel"}:
            stop_mode = str(getattr(ctx, "_group_stop_mode", ""))
    finally:
        progress.setValue(progress.maximum())
        progress.close()
        if hasattr(ctx, "_set_group_action_buttons"):
            ctx._set_group_action_buttons(running=False)
        QApplication.processEvents()

    if stop_mode == "cancel":
        removed = 0
        for d in reversed(created_dirs):
            try:
                if d.exists():
                    shutil.rmtree(d)
                    removed += 1
            except Exception:
                pass
        ctx._log(ctx.group_log, f"已取消抽取合成并回滚: 删除分组 {removed}")
        ctx.refresh_export_folder_list()
        ctx._group_stop_mode = None
        return

    if stop_mode == "stop":
        ctx._log(ctx.group_log, f"抽取合成已中断: 已生成分组 {created}")
        ctx.refresh_export_folder_list()
        ctx._group_stop_mode = None
        return

    ctx._log(
        ctx.group_log,
        f"完成: 生成最终分组 {created} 个；每组按颜色分组顺序各随机抽取 1 张并合成",
    )
    ctx.refresh_export_folder_list()
    ctx._group_stop_mode = None
