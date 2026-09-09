import os
from openai import OpenAI
client=OpenAI(api_key='fixture', base_url=os.environ.get('WMADAPTER_TEST_URL', os.environ.get('WMADAPTER_TEST_URL', 'http://127.0.0.1:18761/v1')))
tools=[{'type':'function','function':{'name':'lookup','parameters':{'type':'object','properties':{}}}}]
messages=[{'role':'user','content':'value?'}]
a=client.chat.completions.create(model='deepseek-chat',messages=messages,tools=tools)
assert a.choices[0].finish_reason=='tool_calls'
call=a.choices[0].message.tool_calls[0]
messages += [a.choices[0].message.model_dump(exclude_none=True), {'role':'tool','tool_call_id':call.id,'content':'42'}]
b=client.chat.completions.create(model='deepseek-chat',messages=messages,tools=tools)
assert b.choices[0].message.content=='42'
chunks=list(client.chat.completions.create(model='deepseek-chat',messages=messages,stream=True))
assert chunks[-1].choices[0].finish_reason=='stop'
print('Hermes installed OpenAI Python SDK: text/tool/result/SSE PASS')
