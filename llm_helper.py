import os
import json
from openai import OpenAI

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

SYSTEM_PROMPT = """
You are a smart, friendly AI receptionist for a barber shop.

Understand the user message and return ONLY JSON.

Intents:
- menu
- book
- choose_service
- choose_barber
- choose_time
- cancel
- reschedule
- change_barber
- smalltalk
- unknown

Return format:
{
  "intent": "...",
  "service": "...",
  "barber": "...",
  "time": "...",
  "name": "..."
}

Rules:
- Be flexible with spelling (e.g. 'milk' → 'mike')
- Extract meaning, not exact words
- If user says thanks/ok → smalltalk
- If unsure → intent = unknown
"""

def llm_extract(text):
    try:
        res = client.chat.completions.create(
            model="gpt-4.1-mini",
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": text}
            ],
            temperature=0.2
        )

        content = res.choices[0].message.content
        return json.loads(content)

    except:
        return {"intent": "unknown"}