import asyncio
from playwright.async_api import async_playwright

BASE = "https://resv.city.meguro.tokyo.jp/Web/"
SKIP = ("checkPurpose", "radioPurpose", "checkdate")

async def settle(page):
    try:
        await page.wait_for_load_state("networkidle", timeout=15000)
    except Exception:
        pass
    await page.wait_for_timeout(1500)

async def dump(page, label):
    print(f"\n===== {label} =====")
    print("URL:", page.url)
    print((await page.inner_text("body"))[:3500])
    els = await page.eval_on_selector_all(
        "input,select,textarea,button",
        "els=>els.map(e=>[e.tagName,e.type||'',e.id||'',e.name||'',(e.value||e.innerText||'').trim().slice(0,30),e.checked===undefined?'':String(e.checked)])",
    )
    for e in els:
        if any(s in e[2] for s in SKIP):
            continue
        print(e)
    await page.screenshot(path=f"{label}.png", full_page=True)

async def main():
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
        await page.wait_for_selector("#radioPeriod1month", state="attached", timeout=30000)
        await settle(page)

        # 絞り込み欄を開いて中身を見る
        try:
            await page.locator("#btnShowDetail").click(timeout=5000)
            await settle(page)
        except Exception as e:
            print("詳細ボタンなし:", str(e)[:80])
        await dump(page, "A_filter")

        # 時間帯の画面へ進む（○△の日があればそれを、なければ先頭の日を選ぶ）
        picked = await page.evaluate(
            """()=>{
              const boxes=[...document.querySelectorAll('input[name=checkdate]')];
              let t=boxes.find(b=>{const c=b.closest('td');return c&&/[○△]/.test(c.innerText);})||boxes[0];
              if(!t) return 'none';
              t.click(); return t.id+' '+t.value;}"""
        )
        print("選んだ日:", picked)
        try:
            await page.locator('a:has-text("次へ進む")').first.click(timeout=10000)
            await settle(page)
        except Exception as e:
            print("次へ進む失敗:", str(e)[:100])
        await dump(page, "B_timeslot")
        await browser.close()

asyncio.run(main())
