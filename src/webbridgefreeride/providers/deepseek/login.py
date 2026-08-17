"""DeepSeek authentication helper.

The first version keeps browser authentication session based.
Credentials automation will be added after validating the login flow.
"""


class DeepSeekLogin:
    def __init__(self, page):
        self.page = page

    async def open_login(self):
        await self.page.goto("https://chat.deepseek.com/")

    async def is_authenticated(self):
        # Provider selectors will be finalized during live testing.
        return False
