from __future__ import annotations

import json
import re
from urllib import error, request


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
                "\nСделай новый вариант полного перевода этого же абзаца. "
                "Не сокращай и не пересказывай текст. Избегай формулировок из предыдущего варианта:\n"
                f"{previous_draft}\n"
            )

        prompt = (
            "Задача: перевести текст на русский язык полностью, без сокращения и пересказа. "
            "Сохрани названия продуктов, организаций, стандартов на английском языке. "
            "Не ограничивай длину перевода искусственно: если исходный абзац длинный, переведи его целиком. "
            "Пиши деловым и нейтральным тоном. "
            "Используй профессиональную терминологию ИБ: не делай буквальный перевод терминов, если сомневаешься, то оставь слово на английском языке. "
            "Используй устоявшиеся термины, например: darknet -> даркнет, threat actor -> киберпреступник/злоумышленник, "
            "ransomware -> шифровальщик (ransomware), phishing -> фишинг, malware -> вредоносное ПО, "
            "exploit -> эксплойт, zero-day -> 0-day, vulnerability -> уязвимость, threat intelligence -> третинтел или TI. "
            "Не усиливай утверждения из исходника, не обобщай и не додумывай выгоды/эффекты. "
            "Если в исходном тексте есть маркетинговое заявление компании, передай его нейтрально как факт заявления (например: 'по заявлению компании', 'утверждается, что' итп). "
            "Без эмодзи, без markdown. "
            "Не добавляй ссылку на источник, не упоминай источник и не добавляй служебные подписи."
            f"{rewrite_hint}\n\n"
            f"Заголовок: {title}\n"
            f"Текст: {text}\n"
            f"URL: {source_url}\n"
        )

        body = json.dumps(
            {
                "model": self._model,
                "temperature": 0.2,
                "messages": [
                    {
                        "role": "system",
                        "content": (
                            "Ты профессиональный переводчик-редактор новостей по информационной безопасности. "
                            "Твоя задача — делать полный перевод переданного абзаца на русский язык "
                            "без сокращений, пересказа и добавления фактов."
                        ),
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
