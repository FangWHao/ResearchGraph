# 本地编码数据

两份压缩词表来自 OpenAI 公布的编码文件，仅用于本地文本计数，不包含研究数据。程序读取后校验未压缩原件的 SHA256，再按该编码的 BPE 和正式正则表达式计数；不会在查询期间下载词表。

| 编码 | 原件字节数 | 原件 SHA256 |
|---|---:|---|
| cl100k_base | 1681126 | 223921b76ee99bde995b7ff738513eef100fb51d18c93597a113bcffe865b2a7 |
| o200k_base | 3613922 | 446a9538cb6c348e3516120d7c08b09f57c36495e2acfffe59a5bf8b0cfb1a2d |

来源：[正式编码定义](https://github.com/openai/tiktoken/blob/main/tiktoken_ext/openai_public.py)、[cl100k 原件](https://openaipublic.blob.core.windows.net/encodings/cl100k_base.tiktoken)、[o200k 原件](https://openaipublic.blob.core.windows.net/encodings/o200k_base.tiktoken)。正则表达式和计数实现使用 [tiktoken](https://github.com/openai/tiktoken)，随目录保留其许可证 `LICENSE.tiktoken`。

这些编码不是 Claude 或 DeepSeek 的计数标准。提取模型的完整请求仍使用提供方实测，此目录不参与提取预算。
