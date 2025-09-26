# app/ai_client_gemini.py
import os
import google.generativeai as genai

MODEL = os.getenv("GEMINI_MODEL_ID", "gemini-2.5-flash-lite")

def configure_api(api_key: str):
    genai.configure(api_key=api_key)

def generate_text(prompt: str, **kwargs) -> str:
    m = genai.GenerativeModel(MODEL)
    resp = m.generate_content(prompt, **kwargs)
    return (resp.text or "").strip()
