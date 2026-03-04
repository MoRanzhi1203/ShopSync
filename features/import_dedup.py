from pathlib import Path
from typing import Callable, List, Tuple

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QApplication, QMessageBox, QProgressDialog


def run_import_to_internal_library(
    ctx,
    *,
    list_images: Callable[[Path], List[Path]],
    md5_of_file: Callable[[Path], str],
    ensure_dir: Callable[[Path], None],
    next_image_index_in_folder: Callable[[Path, str], int],
    compress_image_to_under_2mb: Callable[[Path, Path, int], Tuple[bool, int]],
    is_image_file: Callable[[Path], bool],
    save_json: Callable[[Path, object], None],
    max_import_image_bytes: int,
) -> None:
    imported = 0
    skipped_dup = 0
    folders_created = 0
    compressed_count = 0
    compress_saved_bytes = 0

    session_seen_md5 = set(ctx.md5_index.keys())
    folder_images: List[Tuple[Path, str, List[Path]]] = []
    total_images = 0

    src_root = Path(ctx.src_import_line.text().strip())
    if not src_root.exists():
        QMessageBox.warning(ctx, "错误", "导入目录不存在")
        return
    children = [p for p in sorted(src_root.iterdir()) if p.is_dir()]
    if children:
        for source_folder in children:
            group_name = source_folder.name.strip()
            if not group_name:
                continue
            images = list_images(source_folder)
            folder_images.append((source_folder, group_name, images))
            total_images += len(images)
    else:
        # Fallback for single first-level folder import:
        # if selected folder directly contains images, create/use group by this folder name.
        direct_images = list_images(src_root)
        if not direct_images:
            QMessageBox.warning(ctx, "提示", "导入目录下没有子文件夹，也没有可导入图片")
            return
        group_name = src_root.name.strip() or "未命名分组"
        folder_images.append((src_root, group_name, direct_images))
        total_images += len(direct_images)

    progress = QProgressDialog("正在导入并去重...", "", 0, max(1, total_images), ctx)
    progress.setWindowTitle("导入中")
    progress.setCancelButton(None)
    progress.setWindowModality(Qt.ApplicationModal)
    progress.setMinimumDuration(0)
    progress.setAutoClose(False)
    progress.setAutoReset(False)
    progress.show()
    ctx.setEnabled(False)
    QApplication.processEvents()

    processed = 0
    try:
        for source_folder, group_name, images in folder_images:
            if not images:
                ctx._log(ctx.import_log, f"{source_folder.name}: 未发现图片")
                continue

            new_items: List[Tuple[Path, str]] = []
            for img in images:
                processed += 1
                progress.setValue(processed)
                progress.setLabelText(f"正在检测 {img.name} ({processed}/{total_images})")
                QApplication.processEvents()
                h = md5_of_file(img)
                if h in session_seen_md5:
                    skipped_dup += 1
                    continue
                session_seen_md5.add(h)
                new_items.append((img, h))

            if not new_items:
                ctx._log(ctx.import_log, f"{source_folder.name}: 全部重复，跳过")
                continue

            dst_folder = ctx.internal_root / group_name
            existed = dst_folder.exists()
            ensure_dir(dst_folder)
            if not existed:
                folders_created += 1

            next_idx = next_image_index_in_folder(dst_folder, group_name)
            for src_img, h in new_items:
                raw_ext = src_img.suffix.lower()
                dst_name = f"{group_name}{next_idx:03d}{raw_ext}"
                dst = dst_folder / dst_name
                was_compressed, out_size = compress_image_to_under_2mb(src_img, dst, max_import_image_bytes)
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
                    ctx._log(
                        ctx.import_log,
                        f"{src_img.name}: 压缩完成 {src_img.stat().st_size/1024:.1f}KB -> {out_size/1024:.1f}KB",
                    )
                ctx.md5_index[h] = str(dst.relative_to(ctx.root))
                imported += 1
                next_idx += 1
                if imported % 10 == 0:
                    ctx.refresh_internal_group_overview()

            final_count = len([p for p in dst_folder.iterdir() if is_image_file(p)])
            if final_count < 50:
                ctx._log(ctx.import_log, f"{group_name}: 当前总数 {final_count} (<50)，建议后续补图")
            else:
                ctx._log(ctx.import_log, f"{group_name}: 新增 {len(new_items)} 张，当前总数 {final_count} 张")
            ctx.refresh_internal_group_overview()
    finally:
        ctx.setEnabled(True)
        progress.setValue(progress.maximum())
        progress.close()
        QApplication.processEvents()

    save_json(ctx.index_path, ctx.md5_index)
    ctx._log(
        ctx.import_log,
        f"完成: 新增 {imported}，重复跳过 {skipped_dup}，压缩 {compressed_count}，"
        f"节省 {(compress_saved_bytes/1024/1024):.2f}MB，新建内部分组 {folders_created}",
    )
    ctx.refresh_internal_group_list()
