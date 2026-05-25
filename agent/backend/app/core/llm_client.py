#!/usr/bin/env python3
"""LLM Client — 封装 OpenAI API 调用（兼容 VLLM）。"""

import os
from typing import Optional, List, Dict, Any


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
    ) -> str:
        resp = await self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=temperature,
            max_tokens=max_tokens,
        )
        return resp.choices[0].message.content or ""
