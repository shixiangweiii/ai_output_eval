"""通过 OpenAI 兼容接口调用 GLM 的手工示例。"""

import os


def main() -> None:
    """使用环境变量中的凭证执行一次流式请求。"""
    api_key = os.getenv("DASHSCOPE_API_KEY")
    if not api_key:
        raise SystemExit("请先设置 DASHSCOPE_API_KEY")

    try:
        from openai import OpenAI
    except ImportError as exc:
        raise SystemExit(
            "缺少依赖 openai，请安装 评测脚本/requirements-examples.txt"
        ) from exc

    client = OpenAI(
        api_key=api_key,
        base_url=(
            "https://llm-mqyqo1w3s760td4a.cn-beijing.maas.aliyuncs.com/"
            "compatible-mode/v1"
        ),
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
        reasoning = getattr(delta, "reasoning_content", None)
        if reasoning:
            print(reasoning, end="", flush=True)
        if delta.content:
            print(delta.content, end="", flush=True)


if __name__ == "__main__":
    main()
