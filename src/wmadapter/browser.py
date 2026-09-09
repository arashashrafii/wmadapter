class BrowserManager:
    """Playwright browser lifecycle manager."""

    def __init__(self, profile_path: str):
        self.profile_path = profile_path

    async def start(self):
        pass

    async def stop(self):
        pass
