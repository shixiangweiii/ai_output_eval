from openai import OpenAI
import os

client = OpenAI(
    api_key=os.getenv("DASHSCOPE_API_KEY"),  # 替换为你的百炼API Key
    base_url="https://llm-mqyqo1w3s760td4a.cn-beijing.maas.aliyuncs.com/compatible-mode/v1",
)

completion = client.chat.completions.create(
    model="ZHIPU/GLM-5.2",
    messages=[{"role": "user", "content": "你是谁"}],
    extra_body={"enable_thinking": True, "reasoning_effort": "max"},
    stream=True,
)

for chunk in completion:
    if not chunk.choices:
        continue
    delta = chunk.choices[0].delta
    if hasattr(delta, "reasoning_content") and delta.reasoning_content:
        print(delta.reasoning_content, end="", flush=True)
    if hasattr(delta, "content") and delta.content:
        print(delta.content, end="", flush=True)