import asyncio
import os
import re
import time
import urllib.request
from playwright.async_api import async_playwright

BASE = "https://resv.city.meguro.tokyo.jp/Web/"
TOPIC = os.environ.get("NTFY_TOPIC", "")
TEST = os.environ.get("TEST_NOTIFY") == "true"
LOOP_MINUTES = float(os.environ.get("LOOP_MINUTES", "1") or "1")
INTERVAL_SEC = int(os.environ.get("INTERVAL_SEC", "180") or "180")

WEEKDAYS = {
    "checkYobi1": True,   # 月
    "checkYobi2": False,  # 火
    "checkYobi3": False,  # 水
    "checkYobi4": False,  # 木
    "checkYobi5": True,   # 金
    "checkYobi6": True,   # 土
    "checkYobi7": True,   # 日
    "checkYobi8": False,  # 祝
}
WANT_DAYS = "日月金土"


def notify(msg):
    if not TOPIC:
        print("NTFY_TOPIC が未設定です")
        return
    req = urllib.request.Request(
        f"https://ntfy.sh/{TOPIC}",
        data=msg.encode("utf-8"),
        method="POST",
        headers={"Priority": "high"},
    )
    urllib.request.urlopen(req, timeout=20)
    print("通知を送信しました")


async def settle(page):
    try:
        await page.wait_for_load_state("networkidle", timeout=15000)
    except Exception:
        pass
    await page.wait_for_timeout(1200)


async def retry(fn, tries=4, wait=2000, page=None):
    last = None
    for _ in range(tries):
        try:
            return await fn()
        except Exception as e:
            last = e
            if page:
                await page.wait_for_timeout(wait)
    raise last


async def set_checked(page, id_, want):
    r = await page.evaluate(
        """([id,want])=>{const e=document.getElementById(id);
            if(!e) return 'none';
            if(e.checked===want) return 'same';
            e.click(); return 'clicked';}""",
        [id_, want],
    )
    if r == "clicked":
        await settle(page)
    return r


async def check_once(browser):
    """空いている日のリスト（例: ['31日(土)○']）を返す。失敗時は例外。"""
    context = await browser.new_context(viewport={"width": 1280, "height": 1800})
    page = await context.new_page()
    try:
        await page.goto(BASE)
        await settle(page)
        await page.locator("#category_01").click()
        await settle(page)
        await page.locator('label:has-text("宮前公園庭球場")').first.click()
        await page.wait_for_timeout(500)
        await page.locator('a:has-text("次へ進む")').first.click()
        await page.wait_for_selector("#radioPeriod1month", state="attached", timeout=30000)
        await settle(page)

        try:
            await page.locator("#btnShowDetail").click(timeout=5000)
            await settle(page)
        except Exception:
            pass

        targets = {"radioPeriod1month": True, "radioJikan2": True, **WEEKDAYS}
        for id_, want in targets.items():
            await set_checked(page, id_, want)

        await page.wait_for_selector("#btnHyoji", state="attached", timeout=15000)
        await page.evaluate("document.getElementById('btnHyoji').click()")
        await page.wait_for_timeout(4000)
        await settle(page)

        text = await retry(lambda: page.inner_text("body"), page=page)
        start = text.find("定員")
        end = text.find("次へ進む", start)
        region = text[start:end] if start >= 0 else ""
        dates = re.findall(r"(\d{1,2})\s*\n\s*([月火水木金土日])", region)
        row = region.find("庭球場")
        symbols = re.findall(r"[○△×－＊]", region[row:]) if row >= 0 else []
        if len(symbols) == len(dates) + 1:
            symbols = symbols[1:]
        if not dates or len(dates) != len(symbols):
            try:
                await page.screenshot(path="debug.png", full_page=True)
            except Exception:
                pass
            raise RuntimeError(f"解析失敗 日付{len(dates)} 記号{len(symbols)}")

        print(f"日付{len(dates)}件 / 空き候補: ",
              [(f'{d}{w}', s) for (d, w), s in zip(dates, symbols) if s in "○△"])
        return [
            f"{d}日({w}){s}"
            for (d, w), s in zip(dates, symbols)
            if s in "○△" and w in WANT_DAYS
        ]
    finally:
        await context.close()


async def main():
    if TEST:
        notify("テスト通知：目黒テニス空き監視からの通知です")

    deadline = time.time() + LOOP_MINUTES * 60
    prev = set()
    ok_count = 0
    ng_count = 0

    async with async_playwright() as p:
        browser = await p.chromium.launch()
        while True:
            t0 = time.time()
            try:
                found = set(await check_once(browser))
                ok_count += 1
                new = found - prev
                if new:
                    notify("宮前公園庭球場（午後）に空き：" + "、".join(sorted(new)) + "\n" + BASE)
                else:
                    print("新しい空きなし" if not found else "空きは通知済み")
                prev = found
            except Exception as e:
                ng_count += 1
                print("今回の確認は失敗:", str(e)[:200])
            wait = max(INTERVAL_SEC - (time.time() - t0), 30)
            if time.time() + wait > deadline:
                break
            await asyncio.sleep(wait)
        await browser.close()

    print(f"終了: 成功{ok_count}回 / 失敗{ng_count}回")
    if ok_count == 0:
        raise SystemExit(1)


asyncio.run(main())
