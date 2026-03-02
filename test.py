import asyncio
import base64
import time
from pathlib import Path
from typing import Optional

from playwright.async_api import TimeoutError as PWTimeoutError
from playwright.async_api import async_playwright

URL = "https://phototune.ai/remove-watermarks"
MAX_SIZE = 10 * 1024 * 1024  # 10MB


def is_image_file(p: Path) -> bool:
    return p.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"}


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


async def click_download_and_save(page, context, download_selector: str, out_path: Path, timeout_ms: int):
    # 1) 标准浏览器下载事件
    try:
        async with page.expect_download(timeout=30_000) as dl_info:
            await page.locator(download_selector).first.click()
        download = await dl_info.value
        await download.save_as(str(out_path))
        return
    except PWTimeoutError:
        pass

    # 2) 点击后读取 href/blob/data
    await page.locator(download_selector).first.click()

    deadline = time.time() + (timeout_ms / 1000.0)
    while time.time() < deadline:
        ctrl = page.locator(download_selector).first
        href = await ctrl.get_attribute("href", timeout=200)
        if href:
            if href.startswith("data:"):
                await save_data_url(href, out_path)
                return
            if href.startswith("blob:"):
                await save_blob_url(page, href, out_path)
                return
            if href.startswith("http://") or href.startswith("https://"):
                resp = await context.request.get(href, timeout=timeout_ms)
                if not resp.ok:
                    raise RuntimeError(f"下载直链失败，状态码: {resp.status}")
                out_path.write_bytes(await resp.body())
                return
        await page.wait_for_timeout(400)

    raise PWTimeoutError("已点击 Download Result，但未拿到可下载内容")


async def remove_one(context, in_path: Path, out_dir: Path, timeout_ms: int) -> Path:
    if in_path.stat().st_size > MAX_SIZE:
        raise ValueError(f"文件超过 10MB：{in_path.name}")

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

        await page.set_input_files(file_input, str(in_path))

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

        out_path = out_dir / in_path.name
        await click_download_and_save(page, context, download_selector, out_path, timeout_ms)
        return out_path
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

    print(f"找到 {len(files)} 张，输出目录：{out_dir}")

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=headless)
        context = await browser.new_context(accept_downloads=True)

        ok, fail = 0, 0
        for i, img in enumerate(files, 1):
            try:
                print(f"[{i}/{len(files)}] {img.name}")
                saved = await remove_one(context, img, out_dir, timeout_ms)
                print(f"  -> 已保存：{saved}")
                ok += 1
            except Exception as e:
                print(f"  !! 失败：{img.name} | {type(e).__name__}: {e}")
                fail += 1

        await context.close()
        await browser.close()

    print(f"完成。成功={ok}，失败={fail}")


if __name__ == "__main__":
    asyncio.run(
        batch_remove_watermarks_async(
            input_dir=r".\input_images",
            output_dir=r".\output_images",
            headless=False,
            timeout_ms=240_000,
        )
    )
