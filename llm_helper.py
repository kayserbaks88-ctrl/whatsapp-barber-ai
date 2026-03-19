import os
from openai import OpenAI

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

def llm_extract(message: str):
    prompt = f"""
You are an AI receptionist for a barbershop.

Extract structured data from the message.

Message:
"{message}"

Return ONLY JSON with:
- intent (book, cancel, question, greeting, other)
- service (haircut, skin fade, beard trim, kids cut)
- time (natural language, e.g. "tomorrow 3pm")
- name (if provided)

If not present, return null.

Example:
User: "book kids cut tomorrow 1pm"
Output:
{{
  "intent": "book",
  "service": "kids cut",
  "time": "tomorrow 1pm",
  "name": null
}}
"""

    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            temperature=0
        )

        text = response.choices[0].message.content.strip()

        import json
        return json.loads(text)

    except Exception as e:
        print("LLM ERROR:", e)
        return {}
