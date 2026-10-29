import asyncio
import os
import re
import sys
import urllib.request
from playwright.async_api import async_playwright

BASE = "https://resv.city.meguro.tokyo.jp/Web/"
TOPIC = os.environ.get("NTFY_TOPIC", "")
TEST = os.environ.get("TEST_NOTIFY") == "true"

# 対象の曜日（True=見る）
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
        f"https://ntfy.sh/{TOPIC}", data=msg.encode("utf-8"), method="POST"
    )
    urllib.request.urlopen(req, timeout=20)
    print("通知を送信しました")


async def settle(page):
    try:
        await page.wait_for_load_state("networkidle", timeout=15000)
    except Exception:
        pass
    await page.wait_for_timeout(1500)


async def retry(coro_fn, tries=4, wait=2000, page=None):
    last = None
    for _ in range(tries):
        try:
            return await coro_fn()
        except Exception as e:
            last = e
            if page:
                await page.wait_for_timeout(wait)
    raise last


async def debug_dump(page, label):
    print(f"\n--- デバッグ: {label} ---")
    try:
        print("URL:", page.url)
        print((await page.inner_text("body"))[:1500])
    except Exception as e:
        print("デバッグ取得に失敗:", e)
    try:
        await page.screenshot(path="debug.png", full_page=True)
    except Exception:
        pass


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


async def main():
    if TEST:
        notify("テスト通知：目黒テニス空き監視からの通知です")

    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page(viewport={"width": 1280, "height": 1800})
        await page.goto(BASE)
        await settle(page)

        await page.locator("#category_01").click()
        await settle(page)

        await page.locator('label:has-text("宮前公園庭球場")').first.click()
        await page.wait_for_timeout(500)
        await page.locator('a:has-text("次へ進む")').first.click()

        # カレンダー画面の部品が出るまで待つ
        try:
            await page.wait_for_selector("#radioPeriod1month", state="attached", timeout=30000)
        except Exception:
            await debug_dump(page, "カレンダー画面に進めない")
            sys.exit(1)
        await settle(page)
        print("カレンダー画面URL:", page.url)

        # 絞り込み欄を開く
        try:
            await page.locator("#btnShowDetail").click(timeout=5000)
            await settle(page)
        except Exception as e:
            print("詳細ボタンはスキップ:", str(e)[:80])

        # 1項目ずつ設定（1ヶ月・午後・対象曜日）
        targets = {"radioPeriod1month": True, "radioJikan2": True, **WEEKDAYS}
        for id_, want in targets.items():
            r = await set_checked(page, id_, want)
            print(f"  {id_} -> {want}: {r}")

        # 表示ボタン（画面が切り替わるのを待つ）
        try:
            await page.wait_for_selector("#btnHyoji", state="attached", timeout=15000)
            await page.evaluate("document.getElementById('btnHyoji').click()")
        except Exception as e:
            print("表示ボタンの操作に失敗:", str(e)[:200])
            await debug_dump(page, "表示ボタン失敗")
            sys.exit(1)
        await page.wait_for_timeout(4000)
        await settle(page)

        # 設定の確認（失敗しても続行）
        try:
            state = await retry(
                lambda: page.evaluate(
                    """()=>['radioPeriod1month','radioJikan2','checkYobi1','checkYobi5','checkYobi6','checkYobi7','checkYobi2']
                        .map(id=>id+'='+(document.getElementById(id)||{}).checked).join(', ')"""
                ),
                page=page,
            )
            print("絞り込み状態:", state)
        except Exception as e:
            print("絞り込み状態の確認はスキップ:", str(e)[:100])

        try:
            await page.screenshot(path="result.png", full_page=True)
        except Exception:
            pass

        text = await retry(lambda: page.inner_text("body"), page=page)
        start = text.find("定員")
        end = text.find("次へ進む", start)
        region = text[start:end] if start >= 0 else ""
        dates = re.findall(r"(\d{1,2})\s*\n\s*([月火水木金土日])", region)
        row = region.find("庭球場")
        symbols = re.findall(r"[○△×－＊]", region[row:]) if row >= 0 else []
        if len(symbols) == len(dates) + 1:
            symbols = symbols[1:]  # 先頭は定員欄の「－」

        print("日付数:", len(dates), "記号数:", len(symbols))
        print(list(zip(dates, symbols)))

        if not dates or len(dates) != len(symbols):
            print("解析に失敗しました。画面テキスト：")
            print(region[:2000])
            await debug_dump(page, "解析失敗")
            sys.exit(1)

        found = [
            f"{d}日({w}){s}"
            for (d, w), s in zip(dates, symbols)
            if s in "○△" and w in WANT_DAYS
        ]
        if found:
            notify("宮前公園庭球場（午後）に空き：" + "、".join(found) + "\n" + BASE)
        else:
            print("空きなし")
        await browser.close()


asyncio.run(main())
