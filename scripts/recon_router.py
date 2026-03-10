"""Recon v3: Login, navigate formframe to wireless, dump all form elements."""
from __future__ import annotations

import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
from playwright.async_api import async_playwright, Frame

ARTIFACTS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "artifacts")


async def dump_frame_elements(frame: Frame, label: str):
    elements = []
    for tag in ["input", "select", "button", "a", "textarea"]:
        locs = frame.locator(tag)
        count = await locs.count()
        for i in range(count):
            el = locs.nth(i)
            try:
                attrs = {
                    "tag": tag,
                    "name": await el.get_attribute("name") or "",
                    "id": await el.get_attribute("id") or "",
                    "type": await el.get_attribute("type") or "",
                    "value": (await el.get_attribute("value") or "")[:100],
                    "class": await el.get_attribute("class") or "",
                    "visible": await el.is_visible(),
                }
                if tag in ("a", "button", "select"):
                    try:
                        attrs["text"] = (await el.inner_text())[:200]
                    except Exception:
                        attrs["text"] = ""
                if tag == "select":
                    options = []
                    opt_locs = el.locator("option")
                    opt_count = await opt_locs.count()
                    for j in range(min(opt_count, 30)):
                        o = opt_locs.nth(j)
                        try:
                            options.append({
                                "value": await o.get_attribute("value") or "",
                                "text": (await o.inner_text())[:50,]
                            })
                        except Exception:
                            pass
                    attrs["options"] = options
                elements.append(attrs)
            except Exception:
                pass
    path = os.path.join(ARTIFACTS, f"recon_elements_{label}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(elements, f, indent=2)
    print(f"  -> {len(elements)} elements -> {path}")
    return elements


async def main():
    load_dotenv()
    router_user = os.environ.get("ROUTER_USER", "admin")
    router_pass = os.environ.get("ROUTER_PASS", "")
    base_url = "http://192.168.1.1"
    os.makedirs(ARTIFACTS, exist_ok=True)

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(ignore_https_errors=True)
        page = await context.new_page()
        page.set_default_timeout(30000)

        # Step 1: Login
        print("[1] Login...")
        await page.goto(base_url, wait_until="domcontentloaded", timeout=30000)
        await page.wait_for_timeout(2000)
        await page.fill("input[name='username']", router_user)
        await page.fill("input[name='password']", router_pass)
        await page.locator("a:has-text('LOG IN')").click()

        try:
            await page.wait_for_url("**/index.htm*", timeout=15000)
            print(f"  -> OK: {page.url}")
        except Exception:
            await page.wait_for_url("**/start.htm*", timeout=10000)
            print(f"  -> OK: {page.url}")

        await page.wait_for_timeout(3000)

        # Step 2: Get formframe and navigate to wireless
        print("[2] Navigate formframe to WLG_wireless.htm...")
        formframe = page.frame("formframe")
        if not formframe:
            print("  -> ERROR: formframe not found. Frames:")
            for f in page.frames:
                print(f"     {f.name} -> {f.url}")
            await browser.close()
            return

        print(f"  -> formframe URL: {formframe.url}")
        await formframe.goto(f"{base_url}/WLG_wireless.htm", wait_until="domcontentloaded", timeout=15000)
        await formframe.wait_for_load_state("networkidle", timeout=15000)
        await page.wait_for_timeout(3000)
        print(f"  -> formframe URL after nav: {formframe.url}")

        # Screenshot the whole page (shows frameset with wireless form)
        ss = os.path.join(ARTIFACTS, "recon_wireless_full.png")
        await page.screenshot(path=ss, full_page=True)
        print(f"  -> screenshot: {ss}")

        # Save wireless frame HTML
        html_content = await formframe.content()
        html_path = os.path.join(ARTIFACTS, "recon_wireless_form.html")
        with open(html_path, "w", encoding="utf-8") as f:
            f.write(html_content)
        print(f"  -> HTML: {html_path}")

        # Step 3: Dump all form elements in the wireless frame
        print("[3] Dumping wireless form elements...")
        elements = await dump_frame_elements(formframe, "wireless_form")

        # Print visible elements for quick inspection
        print("\n=== VISIBLE FORM ELEMENTS ===")
        for el in elements:
            if el.get("visible"):
                tag = el["tag"]
                name = el.get("name", "")
                eid = el.get("id", "")
                etype = el.get("type", "")
                val = el.get("value", "")[:50]
                text = el.get("text", "")[:50]
                opts = el.get("options", [])
                if tag == "select":
                    opt_str = ", ".join(o.get("text", "") for o in opts[:5])
                    print(f"  {tag} name={name} id={eid} options=[{opt_str}...]")
                elif tag in ("a", "button"):
                    print(f"  {tag} text='{text}' id={eid}")
                else:
                    print(f"  {tag} name={name} id={eid} type={etype} value='{val}'")

        print("\n[4] Done!")
        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
