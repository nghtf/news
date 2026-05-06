from __future__ import annotations

import json
import re
from pathlib import Path
from urllib import error, request


PROMPTS_DIR = Path(__file__).with_name("prompts")
SYSTEM_PROMPT = (PROMPTS_DIR / "system.txt").read_text(encoding="utf-8").strip()
USER_PROMPT_TEMPLATE = (PROMPTS_DIR / "user.txt").read_text(encoding="utf-8").strip()


class LLMClient:
    def __init__(self, provider: str, api_key: str, model: str) -> None:
        if provider != "openai":
            raise ValueError(f"Unsupported LLM provider for MVP: {provider}")
        self._api_key = api_key
        self._model = model

    def make_short_ru_news(self, title: str, text: str, source_url: str, previous_draft: str | None = None) -> str:
        rewrite_hint = ""
        if previous_draft:
            rewrite_hint = (
                "Сделай новый вариант полного перевода этого же абзаца. "
                "Не сокращай и не пересказывай текст. Избегай формулировок из предыдущего варианта:\n"
                f"{previous_draft}\n"
            )

        prompt = USER_PROMPT_TEMPLATE.format(
            rewrite_hint=rewrite_hint,
            title=title,
            text=text,
            source_url=source_url,
        )

        body = json.dumps(
            {
                "model": self._model,
                "temperature": 0.2,
                "messages": [
                    {
                        "role": "system",
                        "content": SYSTEM_PROMPT,
                    },
                    {
                        "role": "user",
                        "content": prompt,
                    },
                ],
            }
        )
        req = request.Request(
            url="https://api.openai.com/v1/chat/completions",
            data=body.encode("utf-8"),
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self._api_key}",
            },
        )
        try:
            with request.urlopen(req, timeout=45) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
        except error.HTTPError as exc:
            details = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"OpenAI API HTTP {exc.code}: {details}") from exc
        except error.URLError as exc:
            raise RuntimeError(f"OpenAI API connection error: {exc.reason}") from exc

        out = self._extract_chat_content(payload)
        if not out:
            raise RuntimeError(f"LLM returned empty response: {json.dumps(payload, ensure_ascii=False)[:800]}")
        return self._normalize_news_text(out)

    @staticmethod
    def _extract_chat_content(payload: dict) -> str:
        choices = payload.get("choices")
        if not isinstance(choices, list) or not choices:
            return ""
        message = choices[0].get("message", {})
        content = message.get("content", "")
        if isinstance(content, str):
            return content.strip()
        if isinstance(content, list):
            parts: list[str] = []
            for chunk in content:
                if isinstance(chunk, dict) and chunk.get("type") == "text":
                    value = chunk.get("text")
                    if isinstance(value, str):
                        parts.append(value)
            return "\n".join(parts).strip()
        return ""

    @staticmethod
    def to_channel_text(text: str) -> str:
        cleaned = re.sub(r"\s+", " ", text).strip()
        return cleaned

    @staticmethod
    def _normalize_news_text(text: str) -> str:
        base = text.strip()
        if not base:
            return ""
        lines = []
        for line in base.splitlines():
            if re.match(r"^\s*(источник|source)\s*:", line, flags=re.IGNORECASE):
                continue
            lines.append(line)
        normalized = re.sub(r"\s+", " ", "\n".join(lines)).strip()
        return normalized
