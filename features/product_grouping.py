import random
import shutil
import time
from contextlib import nullcontext
from pathlib import Path
from typing import Callable, Dict, List, Optional, Set, Tuple, Type

from PIL import Image
from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QApplication, QListWidgetItem, QMessageBox, QProgressDialog

from features.utils import (
    IMAGE_EXTS,
    apply_watermark,
    ensure_dir,
    is_image_file,
    load_json,
    next_folder_index,
)


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


def _build_folder_plans(
    *,
    folder_count: int,
    group_image_pool: List[Tuple[Path, List[Path]]],
    prefix: str,
    base_idx: int,
) -> List[Dict[str, object]]:
    folder_plans: List[Dict[str, object]] = []
    for i in range(folder_count):
        folder_name = f"{prefix}{base_idx + i:04d}"
        items: List[Dict[str, object]] = []
        for seq, (group_dir, pool) in enumerate(group_image_pool, 1):
            src = random.choice(pool)
            item_key = f"{folder_name}:{seq:03d}:{src.as_posix()}"
            items.append(
                {
                    "item_key": item_key,
                    "folder_name": folder_name,
                    "seq": seq,
                    "src": src,
                    "source_group_name": group_dir.name,
                }
            )
        folder_plans.append({"folder_name": folder_name, "items": items})
    return folder_plans


def _flatten_folder_plans(folder_plans: List[Dict[str, object]]) -> List[Dict[str, object]]:
    picked_items: List[Dict[str, object]] = []
    for folder_plan in folder_plans:
        items = folder_plan.get("items", [])
        if isinstance(items, list):
            picked_items.extend(items)
    return picked_items


def build_second_layer(
    ctx,
    *,
    watermark_config_cls: Type,
    apply_all_watermark: bool = True,
    select_watermark_targets: Optional[Callable[[List[Dict[str, object]]], Optional[Set[str]]]] = None,
) -> None:

    def _wait_if_paused(progress: QProgressDialog, done: int, total_tasks: int) -> bool:
        while bool(getattr(ctx, "_group_paused", False)):
            if getattr(ctx, "_group_stop_mode", None) in {"stop", "cancel"}:
                return True
            progress.setLabelText("已暂停 \n 总进度: {}/{}".format(done, total_tasks))
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
        QMessageBox.warning(ctx, "错误", "未找到水印文件: {}".format(wm_stem))
        return
    wm_path = ctx.watermark_root / wm_file
    if not wm_path.exists():
        QMessageBox.warning(ctx, "错误", "水印文件不存在: {}".format(wm_file))
        return

    cfg_data = load_json(ctx.config_root / f"{cfg_name}.json", None)
    if not isinstance(cfg_data, dict):
        QMessageBox.warning(ctx, "错误", "配置读取失败: {}".format(cfg_name))
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

    group_image_pool: List[Tuple[Path, List[Path]]] = []
    for d in color_group_dirs:
        imgs = [
            p for p in sorted(d.iterdir())
            if p.is_file() and p.suffix.lower() in IMAGE_EXTS
        ]
        if not imgs:
            QMessageBox.warning(ctx, "提示", "颜色分组为空: {}".format(d.name))
            return
        group_image_pool.append((d, imgs))

    folder_count = int(ctx.group_count.value()) if hasattr(ctx, "group_count") else 1
    folder_count = max(1, folder_count)
    cache_root = Path(ctx.second_line.text().strip()) if hasattr(ctx, "second_line") else ctx.second_root
    ensure_dir(cache_root)
    prefix = f"{wm_stem}_{cfg_name}_"
    base_idx = next_folder_index(cache_root, prefix)

    folder_plans = _build_folder_plans(
        folder_count=folder_count,
        group_image_pool=group_image_pool,
        prefix=prefix,
        base_idx=base_idx,
    )
    picked_items = _flatten_folder_plans(folder_plans)
    if not picked_items:
        QMessageBox.information(ctx, "提示", "没有可输出的抽取结果")
        return

    is_group_enabled = getattr(ctx, "_is_internal_group_watermark_enabled", lambda _name: True)
    group_enabled_item_keys = {
        str(entry["item_key"])
        for entry in picked_items
        if bool(is_group_enabled(str(entry.get("source_group_name", ""))))
    }

    if apply_all_watermark:
        selected_item_keys = set(group_enabled_item_keys)
        ctx._log(ctx.group_log, "随机抽取完成: {} 张，按分组设置自动处理水印".format(len(picked_items)))
    else:
        ctx._log(ctx.group_log, "随机抽取完成: {} 张，等待手动选择需要添加水印的图片".format(len(picked_items)))
        selector = select_watermark_targets
        selected_item_keys = selector(picked_items) if selector else set()
        if selected_item_keys is None:
            ctx._log(ctx.group_log, "随机抽取已取消：未确认合成")
            ctx._group_stop_mode = None
            return
        selected_item_keys = set(selected_item_keys) & group_enabled_item_keys

    total_tasks = len(picked_items)
    done = 0
    created_dirs: List[Path] = []
    stop_mode = ""
    watermarked_count = 0
    copied_count = 0

    progress = QProgressDialog("正在合成输出...", "", 0, total_tasks, ctx)
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
        wm_context = Image.open(wm_path) if selected_item_keys else nullcontext(None)
        with wm_context as wm_img:
            for folder_plan in folder_plans:
                folder_name = str(folder_plan["folder_name"])
                entries = list(folder_plan["items"])
                if _wait_if_paused(progress, done, total_tasks):
                    stop_mode = str(getattr(ctx, "_group_stop_mode", ""))
                    break
                if getattr(ctx, "_group_stop_mode", None) in {"stop", "cancel"}:
                    stop_mode = str(getattr(ctx, "_group_stop_mode", ""))
                    break

                out_dir = cache_root / folder_name
                ensure_dir(out_dir)
                created_dirs.append(out_dir)
                ok = 0
                total = len(entries)

                for entry in entries:
                    if _wait_if_paused(progress, done, total_tasks):
                        stop_mode = str(getattr(ctx, "_group_stop_mode", ""))
                        break
                    if getattr(ctx, "_group_stop_mode", None) in {"stop", "cancel"}:
                        stop_mode = str(getattr(ctx, "_group_stop_mode", ""))
                        break

                    src = Path(str(entry["src"]))
                    seq = int(entry["seq"])
                    item_key = str(entry["item_key"])
                    done += 1
                    progress.setValue(done)
                    progress.setLabelText("正在输出 {} ({}/{})\n总进度: {}/{}".format(folder_name, seq, total, done, total_tasks))
                    QApplication.processEvents()

                    try:
                        if item_key in selected_item_keys:
                            with Image.open(src) as im:
                                out = apply_watermark(im, wm_img, cfg)
                            dst = out_dir / f"{folder_name}{seq:03d}.jpg"
                            out.save(dst, "JPEG", quality=95, optimize=True)
                            watermarked_count += 1
                        else:
                            dst = out_dir / f"{folder_name}{seq:03d}{src.suffix.lower() or '.jpg'}"
                            shutil.copy2(src, dst)
                            copied_count += 1
                        ok += 1
                    except Exception as exc:
                        ctx._log(ctx.group_log, "{}: 跳过 {} ({})".format(folder_name, src.name, type(exc).__name__))

                ctx._log(ctx.group_log, "{}: 输出 {}/{} 张".format(folder_name, ok, total))
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
        ctx._log(ctx.group_log, "已取消随机抽取/合成并回滚: 删除分组 {}".format(removed))
        ctx.refresh_export_folder_list()
        ctx._group_stop_mode = None
        return

    if stop_mode == "stop":
        ctx._log(ctx.group_log, "随机抽取/合成已中断: 已生成分组 {}".format(len(created_dirs)))
        ctx.refresh_export_folder_list()
        ctx._group_stop_mode = None
        return

    ctx._log(
        ctx.group_log,
        "完成: 生成最终分组 {} 个；添加水印 {} 张；直接输出 {} 张".format(
            len(created_dirs), watermarked_count, copied_count
        ),
    )
    ctx.refresh_export_folder_list()
    ctx._group_stop_mode = None
