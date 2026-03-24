from openai import OpenAI
import os

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

SYSTEM_PROMPT = """
You are a smart WhatsApp barber booking assistant.

You understand natural language and extract intent.

Return JSON only.

Possible intents:
- book
- reschedule
- cancel
- availability
- greeting
- thanks
- unknown

Extract:
- service
- barber
- time (natural text)

Examples:

User: "book haircut tomorrow 3pm"
→ {"intent":"book","service":"haircut","time":"tomorrow 3pm"}

User: "cancel my booking"
→ {"intent":"cancel"}

User: "any slots after 2?"
→ {"intent":"availability","time":"after 2pm"}

User: "thanks"
→ {"intent":"thanks"}

User: "hi"
→ {"intent":"greeting"}
"""

def llm_extract(text: str):
    try:
        response = client.chat.completions.create(
            model="gpt-4.1-mini",
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": text}
            ],
            temperature=0
        )

        return eval(response.choices[0].message.content)

    except Exception as e:
        print("LLM ERROR:", e)
        return {"intent": "unknown"}