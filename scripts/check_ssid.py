"""Quick check: verify per-band SSID and channel values on the router."""
import asyncio, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dotenv import load_dotenv
from playwright.async_api import async_playwright

async def check():
    load_dotenv()
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context(ignore_https_errors=True)
        page = await ctx.new_page()
        await page.goto("http://192.168.1.1", wait_until="domcontentloaded", timeout=30000)
        await page.wait_for_timeout(2000)
        await page.fill("input[name='username']", os.environ.get("ROUTER_USER", "admin"))
        await page.fill("input[name='password']", os.environ.get("ROUTER_PASS", ""))
        await page.locator("a:has-text('LOG IN')").click()
        await page.wait_for_url("**/index.htm*", timeout=15000)
        await page.wait_for_timeout(2000)
        ff = page.frame("formframe")
        await ff.goto("http://192.168.1.1/WLG_wireless.htm", wait_until="domcontentloaded", timeout=15000)
        await ff.wait_for_load_state("networkidle", timeout=15000)
        await page.wait_for_timeout(2000)

        for band, ssid_sel, ch_sel in [
            ("2.4G", "input[name='ssid']", "select[name='w_channel']"),
            ("5G",   "input[name='ssid_an']", "select[name='w_channel_an']"),
            ("6G",   "input[name='ssid_6g']", "select[name='w_channel_6g']"),
        ]:
            ssid = await ff.locator(ssid_sel).get_attribute("value")
            ch_loc = ff.locator(ch_sel)
            ch_val = await ch_loc.evaluate("el => el.options[el.selectedIndex]?.text || el.value")
            print(f"{band}: SSID={ssid}, Channel={ch_val}")

        await browser.close()

asyncio.run(check())
