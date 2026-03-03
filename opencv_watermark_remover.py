from pathlib import Path

import cv2
import numpy as np


def cv_read_image(path: Path, flags: int) -> np.ndarray:
    data = np.fromfile(str(path), dtype=np.uint8)
    if data.size == 0:
        return None
    return cv2.imdecode(data, flags)


def cv_write_jpg(path: Path, image: np.ndarray, quality: int = 95) -> None:
    ok, encoded = cv2.imencode('.jpg', image, [cv2.IMWRITE_JPEG_QUALITY, quality])
    if not ok:
        raise RuntimeError(f"Failed to encode jpg: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded.tofile(str(path))


def load_watermark_mask(mask_path: Path) -> np.ndarray:
    mask_img = cv_read_image(mask_path, cv2.IMREAD_UNCHANGED)
    if mask_img is None:
        raise FileNotFoundError(f"Mask image not found or unreadable: {mask_path}")

    # Prefer alpha channel from watermark template; fallback to grayscale threshold.
    if mask_img.ndim == 3 and mask_img.shape[2] == 4:
        alpha = mask_img[:, :, 3]
        mask = (alpha > 0).astype(np.uint8) * 255
    else:
        gray = cv2.cvtColor(mask_img, cv2.COLOR_BGR2GRAY) if mask_img.ndim == 3 else mask_img
        mask = (gray > 0).astype(np.uint8) * 255

    # Expand mask slightly for cleaner inpainting borders.
    kernel = np.ones((3, 3), np.uint8)
    return cv2.dilate(mask, kernel, iterations=1)


def inpaint_image(image_path: Path, mask: np.ndarray, out_path: Path) -> None:
    image = cv_read_image(image_path, cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError(f"Image unreadable: {image_path}")

    if image.shape[:2] != mask.shape[:2]:
        raise ValueError(
            f"Size mismatch: image={image.shape[:2]}, mask={mask.shape[:2]}, file={image_path.name}"
        )

    result = cv2.inpaint(image, mask, 3, cv2.INPAINT_TELEA)
    cv_write_jpg(out_path, result, quality=95)


def main() -> None:
    root = Path(__file__).resolve().parent
    input_dir = root / "input_images" / "尺寸"
    output_dir = root / "output_images" / "尺寸_nowatermark"
    mask_path = root / "水印.png"

    if not input_dir.exists():
        raise FileNotFoundError(f"Input folder not found: {input_dir}")
    if not mask_path.exists():
        raise FileNotFoundError(f"Mask image not found: {mask_path}")

    mask = load_watermark_mask(mask_path)
    images = sorted(
        p
        for p in input_dir.iterdir()
        if p.is_file() and p.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}
    )

    print(f"Found {len(images)} images in: {input_dir}")
    print(f"Mask: {mask_path.name}, mask_pixels={int((mask > 0).sum())}")

    ok = 0
    for idx, img_path in enumerate(images, 1):
        out_name = f"尺寸{idx:03d}.jpg"
        out_path = output_dir / out_name
        try:
            inpaint_image(img_path, mask, out_path)
            ok += 1
            print(f"[{idx}/{len(images)}] OK  {img_path.name} -> {out_name}")
        except Exception as exc:
            print(f"[{idx}/{len(images)}] ERR {img_path.name}: {type(exc).__name__}: {exc}")

    print(f"Done. success={ok}, total={len(images)}, output={output_dir}")


if __name__ == "__main__":
    main()
