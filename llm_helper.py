import os
import json
import re
from openai import OpenAI

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

SYSTEM_PROMPT = """
You are an AI booking assistant for a barber shop.

Extract booking info from the user's message.

Return ONLY valid JSON in this exact shape:

{
  "intent": "book" | "view" | "cancel" | "reschedule" | "unknown",
  "service": "haircut" | "beard trim" | "skin fade" | null,
  "barber": "jay" | "mike" | null,
  "when_text": string | null,
  "name": string | null
}

Rules:
- If the message is about making an appointment, intent = "book"
- If the message is about seeing bookings, intent = "view"
- If the message is about cancelling, intent = "cancel"
- If the message is about moving/changing a booking, intent = "reschedule"
- If unsure, intent = "unknown"
- Return JSON only, no markdown, no explanation
"""

EMPTY_RESULT = {
    "intent": "unknown",
    "service": None,
    "barber": None,
    "when_text": None,
    "name": None,
}


def _extract_json(text: str) -> dict:
    text = (text or "").strip()

    if not text:
        return EMPTY_RESULT.copy()

    # Try full response as JSON first
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            return {**EMPTY_RESULT, **data}
    except Exception:
        pass

    # Try to find first {...} block
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        try:
            data = json.loads(match.group(0))
            if isinstance(data, dict):
                return {**EMPTY_RESULT, **data}
        except Exception:
            pass

    return EMPTY_RESULT.copy()


def llm_extract(text: str) -> dict:
    try:
        res = client.chat.completions.create(
            model="gpt-4.1-mini",
            temperature=0,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": text},
            ],
        )

        content = res.choices[0].message.content or ""
        data = _extract_json(content)

        # Normalise values
        if isinstance(data.get("service"), str):
            data["service"] = data["service"].strip().lower()

        if isinstance(data.get("barber"), str):
            data["barber"] = data["barber"].strip().lower()

        if isinstance(data.get("intent"), str):
            data["intent"] = data["intent"].strip().lower()
        else:
            data["intent"] = "unknown"

        if isinstance(data.get("when_text"), str):
            data["when_text"] = data["when_text"].strip()

        if isinstance(data.get("name"), str):
            data["name"] = data["name"].strip()

        return data

    except Exception as e:
        print("LLM ERROR:", e)
        return EMPTY_RESULT.copy()