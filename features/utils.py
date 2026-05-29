import hashlib
import io
import json
import os
import shutil
import sys
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from PIL import Image, ImageOps
from PyQt5.QtCore import QFile, QIODevice
from PyQt5.QtGui import QImage, QPixmap


IMAGE_EXTS: set[str] = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff"}
MAX_IMPORT_IMAGE_BYTES: int = 2 * 1024 * 1024


def ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def app_resource_base_dir() -> Path:
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        try:
            return Path(getattr(sys, "_MEIPASS")).resolve()
        except Exception:
            pass
    return Path(__file__).resolve().parent


def app_data_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        try:
            return Path(sys.executable).resolve().parent
        except Exception:
            pass
    return Path(__file__).resolve().parent


def resolve_resource_path(*parts: str) -> Path:
    p = app_resource_base_dir()
    for part in parts:
        p = p / part
    return p


def resolve_data_path(*parts: str) -> Path:
    p = app_data_base_dir()
    for part in parts:
        p = p / part
    return p


def extract_qrc_file_to_path(qrc_path: str, dst: Path) -> bool:
    src = QFile(qrc_path)
    if not src.open(QIODevice.ReadOnly):
        return False
    try:
        data = src.readAll()
    finally:
        src.close()
    ensure_dir(dst.parent)
    try:
        with dst.open("wb") as f:
            f.write(bytes(data))
        return True
    except Exception:
        return False


def is_valid_image(path: Path) -> bool:
    if not path.exists() or not path.is_file():
        return False
    try:
        with Image.open(path) as im:
            im.verify()
        return True
    except Exception:
        return False


def load_json(path: Path, default: Any = None) -> Any:
    if not path.exists():
        return default
    try:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def save_json(path: Path, obj: Any) -> None:
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


def compress_image_to_under_size(
    src: Path,
    dst: Path,
    max_bytes: int = MAX_IMPORT_IMAGE_BYTES,
) -> Tuple[bool, int]:
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

    best = best_quality_under_limit(img, 85, 96)
    if best is None:
        best = best_quality_under_limit(img, 70, 84)

    if best is None:
        w, h = img.size
        for scale in [0.97, 0.94, 0.91, 0.88]:
            nw, nh = max(1, int(w * scale)), max(1, int(h * scale))
            resized = img.resize((nw, nh), Image.Resampling.LANCZOS)
            best = best_quality_under_limit(resized, 75, 95)
            if best is not None:
                break

    if best is None:
        raise RuntimeError(f"打包失败: {src.name}")
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
        suffix = stem[len(base_name):]
        if len(suffix) == 3 and suffix.isdigit():
            max_idx = max(max_idx, int(suffix))
    return max_idx + 1


def pil_to_qpixmap(img: Image.Image) -> QPixmap:
    rgba = img.convert("RGBA")
    w, h = rgba.size
    data = rgba.tobytes("raw", "RGBA")
    qimg = QImage(data, w, h, 4 * w, QImage.Format_RGBA8888)
    return QPixmap.fromImage(qimg.copy())


def trim_alpha_border(img: Image.Image) -> Image.Image:
    rgba = img.convert("RGBA")
    bbox = rgba.getchannel("A").getbbox()
    if not bbox:
        return rgba
    return rgba.crop(bbox)


def rotate_keep_scale(
    img: Image.Image,
    angle_deg: int,
    *,
    resample: Any = None,
) -> Image.Image:
    if resample is None:
        resample = Image.Resampling.BICUBIC
    src = img.convert("RGBA")
    out = src.rotate(angle_deg % 360, expand=True, resample=resample)
    return trim_alpha_border(out)


def place_mark(
    base_w: int,
    base_h: int,
    mark: Image.Image,
    x_pct: int,
    y_pct: int,
    constrain_inside: bool,
    constrain_bounds: Optional[Tuple[int, int, int, int]] = None,
) -> Tuple[Image.Image, int, int]:
    m = mark
    bx, by, bw, bh = (0, 0, base_w, base_h)
    if constrain_bounds is not None:
        bx, by, bw, bh = constrain_bounds
        bw = max(1, bw)
        bh = max(1, bh)

    if constrain_inside and (m.width > bw or m.height > bh):
        fit_scale = min(bw / float(max(1, m.width)), bh / float(max(1, m.height)))
        nw = max(1, int(round(m.width * fit_scale)))
        nh = max(1, int(round(m.height * fit_scale)))
        m = m.resize((nw, nh), Image.Resampling.LANCZOS)

    cx = int(bx + bw * x_pct / 100)
    cy = int(by + bh * y_pct / 100)
    x = cx - m.width // 2
    y = cy - m.height // 2
    if constrain_inside:
        x = max(bx, min(bx + bw - m.width, x))
        y = max(by, min(by + bh - m.height, y))
    elif constrain_bounds is not None:
        x = max(bx - m.width, min(bx + bw, x))
        y = max(by - m.height, min(by + bh, y))
    return m, x, y


def apply_watermark(img: Image.Image, wm: Image.Image, cfg: Any) -> Image.Image:
    base = img.convert("RGBA")
    mark = wm.convert("RGBA")
    base_rotate = int(getattr(cfg, "wm_base_rotate", 0)) % 360
    base_opacity = max(1, min(100, int(getattr(cfg, "wm_base_opacity", 100))))
    if base_rotate:
        mark = rotate_keep_scale(mark, base_rotate, resample=Image.Resampling.BICUBIC)
    if base_opacity < 100:
        alpha = mark.getchannel("A").point(lambda x: int(x * base_opacity / 100))
        mark.putalpha(alpha)

    target_w = max(1, int(base.width * cfg.scale_pct / 100))
    ratio = target_w / max(1, mark.width)
    target_h = max(1, int(mark.height * ratio))
    mark = mark.resize((target_w, target_h), Image.Resampling.LANCZOS)
    mark = rotate_keep_scale(mark, int(cfg.rotate_deg) % 360, resample=Image.Resampling.BICUBIC)

    if cfg.opacity_pct < 100:
        alpha = mark.getchannel("A").point(lambda x: int(x * cfg.opacity_pct / 100))
        mark.putalpha(alpha)

    mark, x, y = place_mark(
        base.width,
        base.height,
        mark,
        cfg.x_pct,
        cfg.y_pct,
        cfg.constrain_inside,
    )

    layer = Image.new("RGBA", base.size, (0, 0, 0, 0))
    layer.paste(mark, (x, y), mark)
    out = Image.alpha_composite(base, layer)
    return out.convert("RGB")
