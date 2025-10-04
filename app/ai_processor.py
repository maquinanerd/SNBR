#!/usr/bin/env python3
"""
Handles content rewriting using a Generative AI model with API key failover.
"""
import json
import logging
from urllib.parse import urlparse
import time
from pathlib import Path 
from typing import Any, Dict, List, Optional, Tuple, ClassVar

from .config import AI_API_KEYS, SCHEDULE_CONFIG
from .exceptions import AIProcessorError, AllKeysFailedError
from . import ai_client_gemini as ai_client

logger = logging.getLogger(__name__)

AI_SYSTEM_RULES = """
[REGRAS OBRIGATÓRIAS — CUMPRIR 100%]

NÃO incluir e REMOVER de forma explícita:
- Qualquer texto de interface/comentários dos sites (ex.: "Your comment has not been saved").
- Caixas/infobox de ficha técnica com rótulos como: "Release Date", "Runtime", "Director", "Writers", "Producers", "Cast".
- Elementos de comentários, “trending”, “related”, “read more”, “newsletter”, “author box”, “ratings/review box”.

Somente produzir o conteúdo jornalístico reescrito do artigo principal.
Se algum desses itens aparecer no texto de origem, exclua-os do resultado.
"""


class AIProcessor:
    """
    Manages API keys and sends content to the AI model, raising specific exceptions for different failure modes.
    """
    _prompt_template: ClassVar[Optional[str]] = None

    def __init__(self):
        self.api_keys: List[str] = AI_API_KEYS
        if not self.api_keys:
            raise AIProcessorError("No GEMINI_ API keys found.")
        logger.info(f"AI Processor initialized with {len(self.api_keys)} API key(s).")
        self.current_key_index = 0

    def get_current_key_index(self) -> int:
        return self.current_key_index

    def failover_to_next_key(self) -> int:
        """Switches to the next key and returns the new index."""
        self.current_key_index = (self.current_key_index + 1) % len(self.api_keys)
        logger.warning(f"Failing over to next API key index: {self.current_key_index}.")
        return self.current_key_index

    def _build_prompt(self, article: Dict[str, Any], force_json: bool = False) -> str:
        """Builds the prompt for the AI model from an article dictionary."""
        prompt_template = self._load_prompt_template()

        # Extract fields from the article dictionary provided by the feed reader
        title = article.get('title', '')
        source_url = article.get('link', '')
        content_html = article.get('content', '')
        source_name = article.get('source_name', '')
        category = article.get('category', '')
        
        if not source_name and source_url:
            try:
                source_name = urlparse(source_url).netloc.replace("www.", "")
            except Exception:
                source_name = ""

        fields = {
            "titulo_original": title,
            "url_original": source_url,
            "content": content_html,
            "domain": urlparse(source_url).netloc if source_url else "",
            "fonte_nome": source_name,
            "categoria": category,
            "schema_original": "Nenhum",
            "tag": category,
            "tags": category,
            "videos_list": "Nenhum",
            "imagens_list": "Nenhuma",
        }
        return self._safe_format_prompt(prompt_template, fields)

    def send_to_ai_and_validate(self, article: Dict[str, Any], *, force_json: bool = False) -> Dict[str, Any]:
        """
        Sends the article to the model using the current key index.
        Raises Quota429Error or JsonFormatError on specific failures.
        """
        api_key = self.api_keys[self.current_key_index]
        ai_client.configure_api(api_key)

        prompt = self._build_prompt(article, force_json=force_json)
        
        generation_config = {
            "response_mime_type": "application/json" if force_json else "text/plain",
            "temperature": 0.2 if force_json else 0.4,
            "max_output_tokens": 8192, # Using a high value, as per gemini-2.5-flash-lite
        }

        try:
            response_text = ai_client.generate_text(prompt, generation_config=generation_config)
        except ResourceExhausted as e:
            raise Quota429Error(f"HTTP 429 Resource Exhausted for key index {self.current_key_index}") from e
        except GoogleAPICallError as e:
            # Catch other 5xx/network errors
            raise AIProcessorError(f"API Call Error for key index {self.current_key_index}: {e}") from e

        try:
            parsed_data = self._parse_response(response_text)
            if not parsed_data or "erro" in parsed_data:
                 raise JsonFormatError(f"AI response is empty or contains a handled error: {parsed_data.get('erro', '')}")
            # Simple validation, can be expanded
            if not all(k in parsed_data for k in ["titulo_final", "conteudo_final"]):
                raise ValueError("Missing required keys in AI response.")
            return parsed_data
        except (json.JSONDecodeError, ValueError) as e:
            logger.error(f"Failed to parse/validate JSON for key index {self.current_key_index}. Response: {response_text[:200]}...")
            raise JsonFormatError(str(e)) from e

    @staticmethod
    def _parse_response(text: str) -> Optional[Dict[str, Any]]:
        """
        Parses the JSON response from the AI and validates its structure.
        """
        try:
            clean_text = text.strip()
            if clean_text.startswith("```json"):
                clean_text = clean_text[7:-3].strip()
            elif clean_text.startswith("```"):
                clean_text = clean_text[3:-3].strip()

            # Debug: Save raw response to a file
            debug_dir = Path("debug")
            debug_dir.mkdir(exist_ok=True)
            timestamp = time.strftime("%Y%m%d-%H%M%S")
            with open(debug_dir / f"ai_response_{timestamp}.json", "w", encoding="utf-8") as f:
                f.write(clean_text)

            data = json.loads(clean_text)

            if not isinstance(data, dict):
                logger.error(f"AI response is not a dictionary. Received type: {type(data)}")
                return None

            if "erro" in data:
                logger.warning(f"AI returned a rejection error: {data['erro']}")
                return data

            required_keys = [
                "titulo_final", "conteudo_final", "meta_description",
                "focus_keyphrase", "tags_sugeridas", "yoast_meta"
            ]
            missing_keys = [key for key in required_keys if key not in data]

            if missing_keys:
                logger.error(f"AI response is missing required keys: {', '.join(missing_keys)}")
                logger.debug(f"Received data: {data}")
                return None

            if 'yoast_meta' in data and isinstance(data['yoast_meta'], dict):
                required_yoast_keys = [
                    "_yoast_wpseo_title", "_yoast_wpseo_metadesc",
                    "_yoast_wpseo_focuskw", "_yoast_news_keywords"
                ]
                missing_yoast_keys = [key for key in required_yoast_keys if key not in data['yoast_meta']]
                if missing_yoast_keys:
                    logger.error(f"AI response 'yoast_meta' is missing keys: {', '.join(missing_yoast_keys)}")
                    return None
            else:
                logger.error("AI response is missing 'yoast_meta' object or it's not a dictionary.")
                return None

            logger.info("Successfully parsed and validated AI response.")
            return data

        except json.JSONDecodeError as e:
            logger.error(f"Error decoding JSON from AI response: {e}")
            logger.debug(f"Received text: {text[:500]}...")
            return None
        except Exception as e:
            logger.error(f"An unexpected error occurred while parsing AI response: {e}")
            logger.debug(f"Received text: {text[:500]}...")
            return None
