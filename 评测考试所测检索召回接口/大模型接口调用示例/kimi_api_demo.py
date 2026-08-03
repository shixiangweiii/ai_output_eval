"""通过 OpenAI 兼容接口调用 Kimi 视觉模型的手工示例。"""

import os


def main() -> None:
    """使用环境变量中的凭证执行一次图像文字识别请求。"""
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
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
    )
    completion = client.chat.completions.create(
        model="kimi/kimi-k3",
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": (
                                "https://help-static-aliyun-doc.aliyuncs.com/"
                                "file-manage-files/zh-CN/20241108/ctdzex/"
                                "biaozhun.jpg"
                            )
                        },
                    },
                    {"type": "text", "text": "请仅输出图像中的文本内容。"},
                ],
            }
        ],
    )
    print(completion.choices[0].message.content)


if __name__ == "__main__":
    main()
