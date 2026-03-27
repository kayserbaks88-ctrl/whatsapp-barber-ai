import os
import json
from openai import OpenAI

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

SYSTEM_PROMPT = """
You are an AI booking assistant for a barber shop.

Extract structured data from user messages.

Return ONLY valid JSON with:

intent: "book", "view", "cancel", "reschedule", or "unknown"
service: "haircut", "beard trim", etc or null
barber: "jay", "mike", or null
when_text: natural time text like "tomorrow 3pm" or null
name: customer name if mentioned, else null

Do not explain anything. Only JSON.
"""

def llm_extract(text: str):
    try:
        res = client.chat.completions.create(
            model="gpt-4.1-mini",
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": text}
            ],
            temperature=0
        )

        content = res.choices[0].message.content.strip()

        return json.loads(content)

    except Exception as e:
        print("LLM ERROR:", e)
        return {
            "intent": "unknown",
            "service": None,
            "barber": None,
            "when_text": None,
            "name": None
        }