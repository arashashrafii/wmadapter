class DeepSeekAdapter:
    """DeepSeek Web provider adapter.

    MVP placeholder. Browser automation will be implemented here.
    """

    def __init__(self, browser_manager):
        self.browser_manager = browser_manager

    async def chat(self, messages: list[dict]) -> str:
        raise NotImplementedError("DeepSeek browser flow is not implemented yet")
