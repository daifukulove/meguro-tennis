import asyncio
import os
import re
import time
import urllib.request
from datetime import date
from playwright.async_api import async_playwright

BASE = "https://resv.city.meguro.tokyo.jp/Web/"
TOPIC = os.environ.get("NTFY_TOPIC", "")
TEST = os.environ.get("TEST_NOTIFY") == "true"
LOOP_MINUTES = float(os.environ.get("LOOP_MINUTES", "1") or "1")
INTERVAL_SEC = int(os.environ.get("INTERVAL_SEC", "180") or "180")

# カレンダーの表示曜日
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
WANT_WEEKDAYS = {0, 4, 5, 6}  # 月=0 ... 日=6
MIN_START_HOUR = 13           # この時刻以降に始まる枠だけ通知
YOBI = "月火水木金土日"


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


async def shot(page, name):
    try:
        await page.screenshot(path=name, full_page=True)
    except Exception:
        pass


async def check_once(browser):
    """空き枠の {キー: 表示文字列} を返す。失敗時は例外。"""
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

        # 1ヶ月・全日・対象曜日
        targets = {"radioPeriod1month": True, "radioJikan4": True, **WEEKDAYS}
        for id_, want in targets.items():
            await set_checked(page, id_, want)

        await page.wait_for_selector("#btnHyoji", state="attached", timeout=15000)
        await page.evaluate("document.getElementById('btnHyoji').click()")
        await page.wait_for_timeout(4000)
        await settle(page)

        # カレンダーの記号を読む
        text = await retry(lambda: page.inner_text("body"), page=page)
        start = text.find("定員")
        end = text.find("次へ進む", start)
        region = text[start:end] if start >= 0 else ""
        row = region.find("庭球場")
        symbols = re.findall(r"[○△×－＊]", region[row:]) if row >= 0 else []

        vals = await retry(
            lambda: page.evaluate(
                "()=>[...document.querySelectorAll('input[name=checkdate]')].map(e=>e.value)"
            ),
            page=page,
        )
        if len(symbols) == len(vals) + 1:
            symbols = symbols[1:]  # 先頭は定員欄の「－」
        if not vals or len(symbols) != len(vals):
            await shot(page, "debug.png")
            raise RuntimeError(f"カレンダー解析失敗 日付{len(vals)} 記号{len(symbols)}")

        cand = []
        for i, (v, s) in enumerate(zip(vals, symbols)):
            if s not in "○△":
                continue
            y, m, d = int(v[:4]), int(v[4:6]), int(v[6:8])
            if date(y, m, d).weekday() in WANT_WEEKDAYS:
                cand.append((i, y, m, d, s))

        print(f"カレンダー: {len(vals)}日中、候補{len(cand)}日",
              [f"{m}/{d}{s}" for _, _, m, d, s in cand])
        if not cand:
            return {}

        # 候補日を選んで時間帯画面へ
        await page.evaluate(
            """(idx)=>{const b=[...document.querySelectorAll('input[name=checkdate]')];
                idx.forEach(i=>{if(!b[i].checked) b[i].click();});}""",
            [c[0] for c in cand],
        )
        await page.wait_for_timeout(500)
        await page.locator('a:has-text("次へ進む")').first.click()
        await page.wait_for_url("**/WgR_JikantaibetsuAkiJoukyou*", timeout=30000)
        await settle(page)

        # 時間帯画面の表を読む
        tables = await retry(
            lambda: page.evaluate(
                """()=>[...document.querySelectorAll('table')]
                    .filter(t=>!t.querySelector('table'))
                    .map(t=>[...t.rows].map(r=>[...r.cells].map(c=>c.innerText.replace(/\\s+/g,' ').trim())))"""
            ),
            page=page,
        )

        free = {}
        for t in tables:
            if not t:
                continue
            head = t[0]
            m = re.search(r"(\d{4})年(\d{1,2})月(\d{1,2})日", head[0] if head else "")
            if not m:
                continue
            y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
            slots = []
            for c in head:
                mm = re.search(r"(\d{1,2}):(\d{2})\s*～\s*(\d{1,2}):(\d{2})", c)
                if mm:
                    slots.append((int(mm.group(1)), c))
            n = len(slots)
            if n == 0:
                continue
            wd = YOBI[date(y, mo, d).weekday()]
            for r in t[1:]:
                if len(r) < n + 1:
                    continue
                name = r[0] or "コート"
                syms = r[-n:]
                for (hour, label), s in zip(slots, syms):
                    if hour >= MIN_START_HOUR and "○" in s:
                        key = f"{y}{mo:02d}{d:02d}|{label}|{name}"
                        free[key] = f"{mo}/{d}({wd}) {label.replace(' ', '')} {name}"

        print("時間帯画面の空き:", list(free.values()) or "なし")
        if not tables:
            await shot(page, "debug.png")
            raise RuntimeError("時間帯画面の表が見つかりません")
        return free
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
                free = await check_once(browser)
                ok_count += 1
                new = [k for k in free if k not in prev]
                if new:
                    lines = "\n".join(free[k] for k in sorted(new))
                    notify("宮前公園庭球場に空き：\n" + lines + "\n" + BASE)
                else:
                    print("新しい空きなし" if not free else "空きは通知済み")
                prev = set(free)
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
