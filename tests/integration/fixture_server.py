from mimicgate import main
from mimicgate.providers.base import ChatProvider
from mimicgate.providers.router import ProviderRouter

class Fixture(ChatProvider):
    name = 'deepseek'
    model_ids = ('deepseek-chat',)
    async def start(self): pass
    async def stop(self): pass
    async def status(self): return {'ready':True}
    async def complete(self, prompt, conversation_id=None):
        if 'TOOL_RESULT' in prompt: return '42'
        if 'Available tools:' in prompt:
            return '<tool_call>{"name":"lookup","arguments":{}}</tool_call>'
        return 'hello'

main.router = ProviderRouter({'deepseek':Fixture()}, 'deepseek')
app = main.app
