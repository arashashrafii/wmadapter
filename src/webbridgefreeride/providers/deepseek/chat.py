"""DeepSeek web chat automation foundation."""


class DeepSeekChat:
    def __init__(self, page):
        self.page = page

    async def send_message(self, message: str) -> str:
        # Selectors will be implemented after browser inspection.
        # This keeps provider logic isolated from API layer.
        raise NotImplementedError("DeepSeek selectors need validation")
