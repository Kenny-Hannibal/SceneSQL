#!/usr/bin/env python3
"""LLM Client — 封装 OpenAI API 调用（兼容 VLLM），支持流式输出。"""

import os
from typing import Optional, AsyncIterator


class LLMClient:
    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        model: Optional[str] = None,
    ):
        from openai import AsyncOpenAI
        self.client = AsyncOpenAI(
            api_key=api_key or os.getenv("OPENAI_API_KEY", "vllm-local"),
            base_url=base_url or os.getenv("OPENAI_BASE_URL", "http://localhost:30000/v1"),
        )
        self.model = model or os.getenv("AGENT_MAIN_MODEL", "qwen3.5")

    async def chat(
        self,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.1,
        max_tokens: int = 4096,
        response_format: dict | None = None,
    ) -> str:
        kwargs = dict(
            model=self.model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=temperature,
            max_tokens=max_tokens,
        )
        if response_format:
            kwargs["response_format"] = response_format
        resp = await self.client.chat.completions.create(**kwargs)
        return resp.choices[0].message.content or ""

    async def chat_stream(
        self,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.1,
        max_tokens: int = 4096,
    ) -> AsyncIterator[str]:
        """流式调用 LLM，逐 token 返回。"""
        stream = await self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=temperature,
            max_tokens=max_tokens,
            stream=True,
        )
        async for chunk in stream:
            if chunk.choices and chunk.choices[0].delta.content:
                yield chunk.choices[0].delta.content
