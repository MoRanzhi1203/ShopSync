import random
import shutil
from pathlib import Path
from typing import Callable, List, Optional

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QListWidgetItem, QMessageBox


def pick_candidates(ctx, *, list_images: Callable[[Path], List[Path]]) -> None:
    all_images = list_images(ctx.internal_root)
    need = ctx.group_count.value() * ctx.per_group.value()
    if len(all_images) < need:
        QMessageBox.warning(ctx, "提示", f"内部库图片不足。需要 {need}，现有 {len(all_images)}")
        return

    ctx.candidate_images = random.sample(all_images, need)
    random.shuffle(ctx.candidate_images)

    ctx.candidate_list.clear()
    for p in ctx.candidate_images:
        item = QListWidgetItem(str(p.relative_to(ctx.root)))
        item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
        item.setCheckState(Qt.Checked)
        item.setData(Qt.UserRole, str(p))
        ctx.candidate_list.addItem(item)
    ctx._log(ctx.group_log, f"已生成候选 {len(ctx.candidate_images)} 张，可人工取消勾选后再建组")


def resolve_candidate_item_path(ctx, item: QListWidgetItem) -> Optional[Path]:
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
        p2 = ctx.root / txt
        if p2.exists():
            return p2
        name = p.name if p.name else p2.name
        if name:
            hits = list(ctx.internal_root.rglob(name))
            if hits:
                return hits[0]
    return None


def build_second_layer(
    ctx,
    *,
    next_folder_index: Callable[[Path, str], int],
    ensure_dir: Callable[[Path], None],
) -> None:
    checked: List[Path] = []
    for i in range(ctx.candidate_list.count()):
        it = ctx.candidate_list.item(i)
        if it.checkState() == Qt.Checked:
            resolved = resolve_candidate_item_path(ctx, it)
            if resolved is None:
                ctx._log(ctx.group_log, f"跳过无效路径: {it.text()}")
                continue
            checked.append(resolved)

    need = ctx.group_count.value() * ctx.per_group.value()
    if len(checked) < need:
        QMessageBox.warning(ctx, "提示", f"勾选数量不足。需要 {need}，当前 {len(checked)}")
        return

    random.shuffle(checked)
    selected = checked[:need]
    prefix = ctx.second_prefix.text().strip() or "组"

    base_idx = next_folder_index(ctx.second_root, prefix)
    cursor = 0
    created = 0
    for g in range(ctx.group_count.value()):
        folder_name = f"{prefix}{base_idx + g:03d}"
        out_dir = ctx.second_root / folder_name
        ensure_dir(out_dir)
        created += 1

        for i in range(ctx.per_group.value()):
            src = selected[cursor]
            cursor += 1
            ext = src.suffix.lower()
            dst = out_dir / f"{folder_name}_{i + 1:03d}{ext}"
            shutil.copy2(src, dst)

        ctx._log(ctx.group_log, f"{folder_name}: {ctx.per_group.value()} 张")

    ctx._log(ctx.group_log, f"完成: 生成二层文件夹 {created} 个，均为 {ctx.per_group.value()} 张")
    ctx.refresh_export_folder_list()

