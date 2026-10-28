import asyncio
from playwright.async_api import async_playwright

BASE = "https://resv.city.meguro.tokyo.jp/Web/"
SKIP = ("checkPurposeMiddle", "radioPurposeLarge")

async def dump(page, label):
    print(f"\n===== {label} =====")
    print("URL:", page.url)
    print((await page.inner_text("body"))[:3000])
    sel = "a,button,input[type=submit],input[type=button],input[type=checkbox],input[type=radio],select"
    els = await page.eval_on_selector_all(
        sel,
        "els=>els.map(e=>[e.tagName,e.type||'',e.id||'',e.name||'',(e.innerText||e.value||e.alt||'').trim().slice(0,30)])",
    )
    for e in els:
        if any(s in e[2] for s in SKIP):
            continue
        print(e)
    await page.screenshot(path=f"{label}.png", full_page=True)

async def settle(page):
    await page.wait_for_load_state("networkidle")
    await page.wait_for_timeout(1500)

async def click_any(page, selectors):
    for s in selectors:
        loc = page.locator(s).first
        if await loc.count() > 0:
            await loc.click(timeout=10000)
            await settle(page)
            return s
    raise Exception("該当する要素が見つかりません: " + " / ".join(selectors))

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page()
        await page.goto(BASE)
        await settle(page)

        steps = [
            ("sports", ["#category_01"]),
            ("miyamae", ['label:has-text("宮前公園庭球場")',
                         'input[value*="宮前公園庭球場"]',
                         'a:has-text("宮前公園庭球場")',
                         'text=宮前公園庭球場']),
            ("next", ['input[value="次へ進む"]',
                      'button:has-text("次へ進む")',
                      'a:has-text("次へ進む")']),
        ]
        for i, (name, sels) in enumerate(steps, start=1):
            try:
                used = await click_any(page, sels)
                print(f"--- {name}: {used} をクリック")
            except Exception as e:
                print(f"!!! {name} 失敗: {e}")
                await dump(page, f"step{i}_{name}_failed")
                break
            await dump(page, f"step{i}_{name}")
        await browser.close()

asyncio.run(main())
