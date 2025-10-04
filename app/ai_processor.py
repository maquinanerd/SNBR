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
    Handles content rewriting using a Generative AI model with API key failover.
    """
    _prompt_template: ClassVar[Optional[str]] = None

    def __init__(self):
        """
        Initializes the AI processor.
        It uses a single pool of API keys and rotates through them on failure.
        """
        self.api_keys: List[str] = AI_API_KEYS
        if not self.api_keys:
            raise AIProcessorError("No GEMINI_ API keys found in the environment. Please set at least one GEMINI_... key.")

        logger.info(f"AI Processor initialized with {len(self.api_keys)} API key(s).")
        self.current_key_index = 0

    def _failover_to_next_key(self):
        """Switches to the next available API key and returns True if successful."""
        self.current_key_index += 1
        if self.current_key_index >= len(self.api_keys):
            self.current_key_index = 0 # Reset for next cycle
            logger.critical("All API keys have been exhausted. Resetting to first key.")
            return False
        logger.warning(f"Failing over to next API key index: {self.current_key_index}.")
        return True

    @classmethod
    def _load_prompt_template(cls) -> str:
        """Loads the universal prompt from 'universal_prompt.txt'."""
        if cls._prompt_template is None:
            try:
                prompt_path = Path('universal_prompt.txt')
                if not prompt_path.exists():
                    prompt_path = Path(__file__).resolve().parent.parent / 'universal_prompt.txt'

                with open(prompt_path, 'r', encoding='utf-8') as f:
                    base_template = f.read()
                cls._prompt_template = f"{AI_SYSTEM_RULES}\n\n{base_template}"
            except FileNotFoundError:
                logger.critical("'universal_prompt.txt' not found in the project root.")
                raise AIProcessorError("Prompt template file not found.")
        return cls._prompt_template

    @staticmethod
    def _safe_format_prompt(template: str, fields: Dict[str, Any]) -> str:
        """
        Safely formats a string template that may contain literal curly braces.
        """
        class _SafeDict(dict):
            def __missing__(self, key: str) -> str:
                return ""

        s = template.replace('{', '{{').replace('}', '}}')
        for key in fields:
            s = s.replace('{{' + key + '}}', '{' + key + '}')
        
        return s.format_map(_SafeDict(fields))

    def rewrite_content(
        self,
        title: Optional[str] = None,
        content_html: Optional[str] = None,
        source_url: Optional[str] = None,
        category: Optional[str] = None,
        videos: Optional[List[Dict[str, str]]] = None,
        images: Optional[List[str]] = None,
        tags: Optional[List[str]] = None,
        fonte_nome: Optional[str] = None,
        source_name: Optional[str] = None,
        **kwargs: Any,
    ) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
        """
        Rewrites the given article content using the AI model with a robust retry
        and failover mechanism.
        """
        from google.api_core.exceptions import ResourceExhausted

        MAX_RETRIES = 3
        BACKOFF_FACTOR = 60  # seconds

        prompt_template = self._load_prompt_template()

        # Prepare prompt fields
        videos = videos or []
        images = images or []
        tags = tags or []
        fonte = fonte_nome or source_name or ""
        if not fonte and source_url:
            try:
                fonte = urlparse(source_url).netloc.replace("www.", "")
            except Exception:
                fonte = ""

        fields = {
            "titulo_original": title or "",
            "url_original": source_url or "",
            "content": content_html or "",
            "domain": kwargs.get("domain", ""),
            "fonte_nome": fonte,
            "categoria": category or "",
            "schema_original": json.dumps(kwargs.get("schema_original"), indent=2, ensure_ascii=False) if kwargs.get("schema_original") else "Nenhum",
            "tag": (tags[0] if tags else category or ""),
            "tags": (", ".join(tags) if tags else category or ""),
            "videos_list": "\n".join([v.get("embed_url", "") for v in videos if isinstance(v, dict) and v.get("embed_url")]) or "Nenhum",
            "imagens_list": "\n".join(images) if images else "Nenhuma",
        }
        prompt = self._safe_format_prompt(prompt_template, fields)

        last_error = "Unknown error"
        
        # Loop through each API key, allowing retries on each
        for _ in range(len(self.api_keys)):
            api_key = self.api_keys[self.current_key_index]
            ai_client.configure_api(api_key)
            
            retries = 0
            while retries < MAX_RETRIES:
                try:
                    logger.info(f"Sending content to AI. Key index: {self.current_key_index}, Attempt: {retries + 1}/{MAX_RETRIES}")
                    
                    generation_config = {"response_mime_type": "application/json"}
                    response_text = ai_client.generate_text(prompt, generation_config=generation_config)
                    
                    parsed_data = self._parse_response(response_text)

                    if not parsed_data:
                        raise AIProcessorError("Failed to parse or validate AI response.")

                    if "erro" in parsed_data:
                        logger.warning(f"AI returned a handled error: {parsed_data['erro']}")
                        return None, parsed_data["erro"]

                    # SUCCESS: Do not failover, just return the data
                    logger.info(f"Successfully processed content with key index: {self.current_key_index}.")
                    return parsed_data, None

                except ResourceExhausted as e:
                    last_error = str(e)
                    retries += 1
                    if retries >= MAX_RETRIES:
                        logger.warning(f"Rate limit exhausted for key index {self.current_key_index} after {MAX_RETRIES} retries. Failing over.")
                        break  # Break inner loop to failover to the next key

                    wait_time = BACKOFF_FACTOR * (2 ** (retries - 1))
                    logger.warning(f"Rate limit hit (429). Waiting for {wait_time}s before retry {retries}/{MAX_RETRIES}.")
                    time.sleep(wait_time)

                except Exception as e:
                    last_error = str(e)
                    logger.error(f"An unexpected error occurred with key index {self.current_key_index}: {last_error}")
                    # For non-rate-limit errors, fail over immediately
                    break 

            # If we exit the while loop, it means all retries for the current key failed.
            # Time to try the next key.
            if not self._failover_to_next_key():
                break  # All keys have been tried and failed

        final_reason = f"All available API keys failed after retries. Last error: {last_error}"
        logger.critical(final_reason)
        return None, final_reason

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
