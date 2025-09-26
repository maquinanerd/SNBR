import os
from dotenv import load_dotenv
import google.generativeai as genai

load_dotenv()
api_key = (os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
           or os.getenv("GEMINI_POLITICA_1") or os.getenv("GEMINI_ECONOMIA_1")
           or os.getenv("GEMINI_FINANCAS_1") or os.getenv("GEMINI_LEGISLACAO_1"))
assert api_key, "Defina ao menos uma chave no .env"

genai.configure(api_key=api_key)
m = genai.GenerativeModel(os.getenv("GEMINI_MODEL_ID","gemini-2.5-flash-lite"))
print(m.generate_content("Responda apenas: ok").text)