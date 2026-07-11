# -*- coding: utf-8 -*-
"""Playwright-rendered fetches for career pages whose job links only appear
after client-side JavaScript runs. Used by the full cloud scan (JS_WATCH=1
+ playwright installed); silently unavailable elsewhere.
"""
from net import UA


def fetch_rendered(urls, cap=40, timeout_ms=20000):
    """Render each URL in headless Chromium; returns [(url, final_url, html)]."""
    from playwright.sync_api import sync_playwright  # runtime import: optional dep
    out = []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        ctx = browser.new_context(user_agent=UA, locale="de-CH")
        page = ctx.new_page()
        for url in urls[:cap]:
            try:
                page.goto(url, timeout=timeout_ms, wait_until="domcontentloaded")
                page.wait_for_timeout(1500)  # let list widgets populate
                out.append((url, page.url, page.content()))
            except Exception:
                continue
        browser.close()
    return out
