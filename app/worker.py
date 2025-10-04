# app/worker.py
import logging
import time
import random
import json
from urllib.parse import urlparse

from bs4 import BeautifulSoup

from .ai_processor import AIProcessor
from .exceptions import Quota429Error, JsonFormatError, AIProcessorError
from .wordpress import WordPressClient
from .config import (
    BACKOFF_BASE, BACKOFF_MAX, MAX_JSON_RETRY, 
    WORDPRESS_CONFIG, WORDPRESS_CATEGORIES, CATEGORY_ALIASES, RSS_FEEDS
)
from .store import Database
from .extractor import ContentExtractor
from .html_utils import (
    merge_images_into_content,
    rewrite_img_srcs_with_wp,
    strip_credits_and_normalize_youtube,
    remove_broken_image_placeholders,
    strip_naked_internal_links,
)
from .internal_linking import add_internal_links
from .cleaners import clean_html_for_globo_esporte

log = logging.getLogger(__name__)

# Helper functions from the old pipeline
CLEANER_FUNCTIONS = {
    'globo.com': clean_html_for_globo_esporte,
}

def _get_article_url(article_data) -> str | None:
    return article_data.get("url") or article_data.get("link") or article_data.get("id")

def is_valid_upload_candidate(url: str) -> bool:
    if not url:
        return False
    try:
        lower_url = url.lower()
        p = urlparse(lower_url)
        if not p.scheme.startswith("http"): return False
        if p.netloc in {"sb.scorecardresearch.com", "securepubads.g.doubleclick.net"}: return False
        if not p.path.endswith((".jpg", ".jpeg", ".png", ".webp", ".gif")): return False
        if "author" in lower_url or "avatar" in lower_url: return False
        dims = re.findall(r'[?&](?:w|width|h|height)=(\d+)', lower_url)
        if any(int(d) <= 100 for d in dims): return False
        return True
    except Exception:
        return False

def _exp_backoff_sleep(attempt):
    wait = min(BACKOFF_BASE * (2 ** (attempt - 1)), BACKOFF_MAX)
    sleep_time = wait + random.randint(0, 5)
    log.info(f"Exponential backoff: sleeping for {sleep_time} seconds.")
    time.sleep(sleep_time)

def process_one_article(article: dict, ai_processor: AIProcessor, db: Database) -> bool:
    log.info(f"Processing article: {article.get('title', 'N/A')} from source {article.get('source_id')}")
    
    json_retry_used = False
    backoff_attempt = 0

    # These clients are lightweight and can be created per article
    wp_client = WordPressClient(config=WORDPRESS_CONFIG, categories_map=WORDPRESS_CATEGORIES)
    extractor = ContentExtractor()

    article_db_id = article['db_id']
    db.update_article_status(article_db_id, 'PROCESSING')

    while True:
        try:
            # Step 1: Get rewritten content from AI
            # The article dict from the queue already has all the necessary info
            rewritten_data = ai_processor.send_to_ai_and_validate(
                article, 
                force_json=json_retry_used
            )

            # Step 2: The extensive post-processing and publishing logic from the old pipeline
            article_url_to_process = _get_article_url(article)

            # Fetch original content for image extraction etc.
            html_content = extractor._fetch_html(article_url_to_process)
            if not html_content:
                raise Exception("Failed to fetch original HTML for processing.")

            soup = BeautifulSoup(html_content, 'lxml')
            domain = urlparse(article_url_to_process).netloc.lower()
            if cleaner_func := CLEANER_FUNCTIONS.get(domain):
                soup = cleaner_func(soup)

            extracted_data = extractor.extract(str(soup), url=article_url_to_process)
            if not extracted_data or not extracted_data.get('content'):
                raise Exception("Failed to extract content from original article.")

            # --- Start of publishing logic ---
            title = rewritten_data.get("titulo_final", "").strip()
            content_html = rewritten_data.get("conteudo_final", "").strip()

            if not title or not content_html:
                raise Exception("AI output missing required fields.")

            content_html = remove_broken_image_placeholders(content_html)
            content_html = strip_naked_internal_links(content_html)
            content_html = merge_images_into_content(content_html, extracted_data.get('images', []))

            # Image uploading
            urls_to_upload = []
            if featured_url := extracted_data.get('featured_image_url'):
                if is_valid_upload_candidate(featured_url):
                    urls_to_upload.append(featured_url)
            
            uploaded_src_map, uploaded_id_map = {}, {}
            for url in urls_to_upload:
                media = wp_client.upload_media_from_url(url, title)
                if media and media.get("source_url") and media.get("id"):
                    k = url.rstrip('/')
                    uploaded_src_map[k] = media["source_url"]
                    uploaded_id_map[k] = media["id"]

            content_html = rewrite_img_srcs_with_wp(content_html, uploaded_src_map)
            content_html = strip_credits_and_normalize_youtube(content_html)

            source_name = article.get('source_name', urlparse(article_url_to_process).netloc)
            credit_line = f'<p><strong>Fonte:</strong> <a href="{article_url_to_process}" target="_blank" rel="noopener noreferrer">{source_name}</a></p>'
            content_html += f"\n{credit_line}"

            # Category resolution
            final_category_ids = {8, 267} # Futebol, Notícias
            if main_cat_id := WORDPRESS_CATEGORIES.get(article.get('category')):
                final_category_ids.add(main_cat_id)
            
            if suggested_categories := rewritten_data.get('categorias', []):
                suggested_names = [cat['nome'] for cat in suggested_categories if isinstance(cat, dict) and 'nome' in cat]
                normalized_names = [CATEGORY_ALIASES.get(name.lower(), name) for name in suggested_names]
                if dynamic_ids := wp_client.resolve_category_names_to_ids(normalized_names):
                    final_category_ids.update(dynamic_ids)

            # Internal linking
            try:
                with open('data/internal_links.json', 'r', encoding='utf-8') as f:
                    link_map = json.load(f)
                content_html = add_internal_links(content_html, link_map, list(final_category_ids))
            except (FileNotFoundError, json.JSONDecodeError):
                log.warning("Could not load internal link map. Skipping.")

            # Featured media
            featured_media_id = None
            if featured_url := extracted_data.get('featured_image_url'):
                featured_media_id = uploaded_id_map.get(featured_url.rstrip('/'))
            if not featured_media_id and uploaded_id_map:
                featured_media_id = next(iter(uploaded_id_map.values()), None)

            # Yoast & Post Payload
            yoast_meta = rewritten_data.get('yoast_meta', {})
            yoast_meta['_yoast_wpseo_canonical'] = article_url_to_process
            if related_kws := rewritten_data.get('related_keyphrases'):
                yoast_meta['_yoast_wpseo_keyphrases'] = json.dumps([{"keyword": kw} for kw in related_kws])

            post_payload = {
                'title': title, 'slug': rewritten_data.get('slug'),
                'content': content_html, 'excerpt': rewritten_data.get('meta_description', ''),
                'categories': list(final_category_ids), 'tags': rewritten_data.get('tags_sugeridas', []),
                'featured_media': featured_media_id, 'meta': yoast_meta,
            }

            # Create Post
            wp_post_id = wp_client.create_post(post_payload)

            if wp_post_id:
                db.save_processed_post(article_db_id, wp_post_id)
                log.info(f"Successfully published post {wp_post_id} for article DB ID {article_db_id}")
                return True # Success
            else:
                raise Exception("WordPress publishing failed.")

        except Quota429Error:
            backoff_attempt += 1
            log.warning(f"Quota429Error on key {ai_processor.get_current_key_index()} (attempt {backoff_attempt}). Backing off...")
            _exp_backoff_sleep(backoff_attempt)
            if backoff_attempt >= 3:
                ai_processor.failover_to_next_key()
                backoff_attempt = 0 # Reset for the new key

        except JsonFormatError:
            if not json_retry_used and MAX_JSON_RETRY > 0:
                log.warning(f"JsonFormatError on key {ai_processor.get_current_key_index()}. Retrying with force_json=True...")
                json_retry_used = True
                continue # Retry the while loop
            log.error("JsonFormatError even after retry. Discarding article.")
            db.update_article_status(article_db_id, 'FAILED', reason="Invalid JSON from AI")
            return False # Failure

        except Exception as e:
            log.exception(f"Unexpected error processing article DB ID {article_db_id}: {e}")
            db.update_article_status(article_db_id, 'FAILED', reason=str(e))
            return False # Failure
    
    # Should not be reached if the loop is structured correctly
    return False
