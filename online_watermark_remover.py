# -*- coding: utf-8 -*-
"""
在线网页去水印自动化脚本。
功能说明：
1. 自动打开网页并上传图片。
2. 上传成功后自动点击 Remove。
3. 处理完成后等待 Download Result 可点击并自动下载。
4. 下载后统一转换为 .jpg；若文件超过 2MB，自动压缩到小于 2MB。
5. 输出目录默认使用相对路径 .\\output_images，文件名保持原图名（仅后缀改为 .jpg）。
6. 记录并输出每张图的关键时间点与耗时：上传、可下载、保存、总耗时。
"""

import asyncio
import base64
import io
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

from PIL import Image
from playwright.async_api import TimeoutError as PWTimeoutError
from playwright.async_api import async_playwright

URL = "https://phototune.ai/remove-watermarks"
MAX_INPUT_SIZE = 10 * 1024 * 1024  # 10MB
MAX_OUTPUT_SIZE = 2 * 1024 * 1024  # 2MB


def log(msg: str):
    print(msg, flush=True)


def is_image_file(p: Path) -> bool:
    return p.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"}


def ts() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


async def pick_file_input(page) -> Optional[str]:
    candidates = [
        'input[type="file"]',
        'input[accept*="image"]',
    ]
    for sel in candidates:
        if await page.locator(sel).count() > 0:
            return sel
    return None


async def click_if_exists(page, selectors: list[str], timeout_ms: int = 1500) -> bool:
    for sel in selectors:
        loc = page.locator(sel).first
        try:
            if await loc.is_visible(timeout=timeout_ms):
                await loc.click()
                return True
        except Exception:
            pass
    return False


async def wait_enabled_button(page, selectors: list[str], timeout_ms: int, name: str) -> str:
    start = time.time()
    while (time.time() - start) * 1000 < timeout_ms:
        for sel in selectors:
            group = page.locator(sel)
            try:
                if await group.count() == 0:
                    continue
                btn = group.first
                if not await btn.is_visible(timeout=200):
                    continue
                disabled = await btn.get_attribute("disabled", timeout=200)
                aria_disabled = await btn.get_attribute("aria-disabled", timeout=200)
                if disabled is None and aria_disabled not in {"true", "1"}:
                    return sel
            except Exception:
                pass
        await page.wait_for_timeout(400)
    raise PWTimeoutError(f"等待 {name} 按钮超时: {timeout_ms}ms")


async def save_data_url(data_url: str, out_path: Path):
    header, b64_data = data_url.split(",", 1)
    if ";base64" not in header:
        raise RuntimeError("data URL 不是 base64，无法保存")
    out_path.write_bytes(base64.b64decode(b64_data))


async def save_blob_url(page, blob_url: str, out_path: Path):
    js = """
    async (url) => {
      const res = await fetch(url);
      const blob = await res.blob();
      return await new Promise((resolve, reject) => {
        const reader = new FileReader();
        reader.onloadend = () => resolve(reader.result);
        reader.onerror = reject;
        reader.readAsDataURL(blob);
      });
    }
    """
    data_url = await page.evaluate(js, blob_url)
    if not isinstance(data_url, str) or not data_url.startswith("data:"):
        raise RuntimeError("无法从 blob URL 提取内容")
    await save_data_url(data_url, out_path)


def _encode_jpg_bytes(img: Image.Image, quality: int) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=quality, optimize=True, progressive=True)
    return buf.getvalue()


def convert_and_compress_to_jpg(raw_path: Path, final_jpg_path: Path, max_bytes: int = MAX_OUTPUT_SIZE):
    with Image.open(raw_path) as im:
        img = im.convert("RGB")

    best_bytes = None

    for quality in [92, 88, 84, 80, 76, 72, 68, 64, 60, 56, 52, 48, 44, 40, 36, 32, 28, 24]:
        jpg_bytes = _encode_jpg_bytes(img, quality)
        best_bytes = jpg_bytes
        if len(jpg_bytes) <= max_bytes:
            break

    if best_bytes is not None and len(best_bytes) > max_bytes:
        w, h = img.size
        for scale in [0.9, 0.8, 0.7, 0.6, 0.5]:
            nw, nh = max(1, int(w * scale)), max(1, int(h * scale))
            resized = img.resize((nw, nh), Image.Resampling.LANCZOS)
            for quality in [72, 64, 56, 48, 40, 32, 24]:
                jpg_bytes = _encode_jpg_bytes(resized, quality)
                best_bytes = jpg_bytes
                if len(jpg_bytes) <= max_bytes:
                    break
            if len(best_bytes) <= max_bytes:
                break

    if best_bytes is None:
        raise RuntimeError("图片转换失败：未生成 JPG 数据")

    final_jpg_path.write_bytes(best_bytes)


async def click_download_and_save_raw(page, context, download_selector: str, raw_path: Path, timeout_ms: int):
    try:
        async with page.expect_download(timeout=30_000) as dl_info:
            await page.locator(download_selector).first.click()
        download = await dl_info.value
        await download.save_as(str(raw_path))
        return
    except PWTimeoutError:
        pass

    await page.locator(download_selector).first.click()

    deadline = time.time() + (timeout_ms / 1000.0)
    while time.time() < deadline:
        ctrl = page.locator(download_selector).first
        href = await ctrl.get_attribute("href", timeout=200)
        if href:
            if href.startswith("data:"):
                await save_data_url(href, raw_path)
                return
            if href.startswith("blob:"):
                await save_blob_url(page, href, raw_path)
                return
            if href.startswith("http://") or href.startswith("https://"):
                resp = await context.request.get(href, timeout=timeout_ms)
                if not resp.ok:
                    raise RuntimeError(f"下载直链失败，状态码: {resp.status}")
                raw_path.write_bytes(await resp.body())
                return
        await page.wait_for_timeout(400)

    raise PWTimeoutError("已点击 Download Result，但未拿到可下载内容")


async def remove_one(context, in_path: Path, out_dir: Path, timeout_ms: int) -> Path:
    if in_path.stat().st_size > MAX_INPUT_SIZE:
        raise ValueError(f"文件超过 10MB：{in_path.name}")

    final_path = out_dir / f"{in_path.stem}.jpg"
    raw_path = out_dir / f"{in_path.stem}.__raw__"

    total_start = time.perf_counter()
    log(f"  [{ts()}] 开始处理: {in_path.name}")

    page = await context.new_page()
    try:
        await page.goto(URL, wait_until="domcontentloaded", timeout=60_000)

        await click_if_exists(page, [
            "button:has-text('Accept')",
            "button:has-text('I agree')",
            "button:has-text('OK')",
        ])

        file_input = await pick_file_input(page)
        if not file_input:
            raise RuntimeError("未找到上传控件 input[type=file]")

        upload_start = time.perf_counter()
        await page.set_input_files(file_input, str(in_path))
        upload_secs = time.perf_counter() - upload_start
        log(f"  [{ts()}] 上传完成: {in_path.name} | 耗时 {upload_secs:.2f}s")

        remove_selector = await wait_enabled_button(
            page,
            selectors=[
                "button:has-text('Remove')",
                "button:has-text('Remove Watermark')",
                "text=/^Remove$/i",
            ],
            timeout_ms=timeout_ms,
            name="Remove",
        )
        await page.locator(remove_selector).first.click()

        download_selector = await wait_enabled_button(
            page,
            selectors=[
                "button:has-text('Download Result')",
                "a:has-text('Download Result')",
                "button:has-text('Download')",
                "a:has-text('Download')",
                "text=/Download Result/i",
            ],
            timeout_ms=timeout_ms,
            name="Download Result",
        )

        process_secs = time.perf_counter() - upload_start
        log(f"  [{ts()}] 去水印完成(可下载): {in_path.name} | 耗时 {process_secs:.2f}s")

        save_start = time.perf_counter()
        await click_download_and_save_raw(page, context, download_selector, raw_path, timeout_ms)
        convert_and_compress_to_jpg(raw_path, final_path, max_bytes=MAX_OUTPUT_SIZE)
        save_secs = time.perf_counter() - save_start

        if raw_path.exists():
            raw_path.unlink()

        total_secs = time.perf_counter() - total_start
        log(f"  [{ts()}] 保存完成: {final_path.name} | 保存耗时 {save_secs:.2f}s | 总耗时 {total_secs:.2f}s")

        return final_path
    finally:
        await page.close()


async def batch_remove_watermarks_async(
    input_dir: str,
    output_dir: str = r".\output_images",
    headless: bool = True,
    timeout_ms: int = 240_000,
):
    in_dir = Path(input_dir).expanduser()
    out_dir = Path(output_dir).expanduser()
    out_dir.mkdir(parents=True, exist_ok=True)

    files = sorted([p for p in in_dir.iterdir() if p.is_file() and is_image_file(p)])
    if not files:
        raise FileNotFoundError(f"未找到图片：{in_dir}")

    log(f"找到 {len(files)} 张，输出目录：{out_dir}")

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=headless)
        context = await browser.new_context(accept_downloads=True)

        ok, fail = 0, 0
        results: list[tuple[str, str, str]] = []
        for i, img in enumerate(files, 1):
            try:
                log(f"[{i}/{len(files)}] {img.name}")
                saved = await remove_one(context, img, out_dir, timeout_ms)
                size_kb = saved.stat().st_size / 1024
                log(f"  -> 已保存：{saved} ({size_kb:.1f} KB)")
                ok += 1
                results.append((img.name, "OK", str(saved)))
            except Exception as e:
                log(f"  !! 失败：{img.name} | {type(e).__name__}: {e}")
                fail += 1
                results.append((img.name, "FAIL", f"{type(e).__name__}: {e}"))

        await context.close()
        await browser.close()

    log(f"完成。成功={ok}，失败={fail}")
    log("逐文件结果：")
    for name, status, detail in results:
        log(f"  [{status}] {name} -> {detail}")


if __name__ == "__main__":
    asyncio.run(
        batch_remove_watermarks_async(
            input_dir=r".\input_images",
            output_dir=r".\output_images",
            headless=False,
            timeout_ms=240_000,
        )
    )
