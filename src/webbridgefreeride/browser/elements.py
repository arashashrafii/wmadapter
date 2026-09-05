"""Small shared DOM primitive; provider selectors remain provider-owned."""
async def first_visible(page, selectors: list[str], provider: str):
    for selector in selectors:
        locator = page.locator(selector).last
        try:
            if await locator.is_visible(timeout=1500):
                return locator
        except Exception:
            continue
    raise RuntimeError(f"No visible {provider} element found for selectors: {selectors}")
