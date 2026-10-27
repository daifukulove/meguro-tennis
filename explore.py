import asyncio
from playwright.async_api import async_playwright

BASE = "https://resv.city.meguro.tokyo.jp/Web/"

async def dump(page, label):
    print(f"\n===== {label} =====")
    print("URL:", page.url)
    print((await page.inner_text("body"))[:2500])
    sel = "a,button,input[type=submit],input[type=button],input[type=checkbox],input[type=radio]"
    els = await page.eval_on_selector_all(
        sel,
        "els=>els.map(e=>[e.tagName,e.type||'',e.id||'',e.name||'',(e.innerText||e.value||e.alt||'').trim().slice(0,30)])",
    )
    for e in els[:80]:
        print(e)
    await page.screenshot(path=f"{label}.png", full_page=True)

async def click_text(page, text):
    await page.get_by_text(text).first.click()
    await page.wait_for_load_state("networkidle")

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page()
        await page.goto(BASE)
        await page.wait_for_load_state("networkidle")
        await dump(page, "step0_top")
        for i, text in enumerate(["スポーツ施設", "宮前公園庭球場", "次へ進む"], start=1):
            try:
                await click_text(page, text)
            except Exception as e:
                print(f"!!! 「{text}」のクリックに失敗: {e}")
                await dump(page, f"step{i}_failed")
                break
            await dump(page, f"step{i}_{text}")
        await browser.close()

asyncio.run(main())
