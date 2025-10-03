import os
import json
import logging
from app.wordpress import WordPressClient
from app.config import WORDPRESS_CONFIG, WORDPRESS_CATEGORIES

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

OUTPUT_DIR = 'data'
OUTPUT_FILE = os.path.join(OUTPUT_DIR, 'internal_links.json')

def build_map():
    """
    Fetches the latest 1000 published posts from WordPress and builds a 
    structured map containing titles, links, categories, and tags.
    """
    logger.info("Initializing WordPress client for link map generation...")
    if not WORDPRESS_CONFIG.get('url'):
        logger.error("WordPress URL not configured. Aborting.")
        return

    client = WordPressClient(WORDPRESS_CONFIG, WORDPRESS_CATEGORIES)
    
    # Fetch the latest 1000 posts with the necessary data for intelligent linking
    fields_to_fetch = ['id', 'slug', 'title', 'link', 'categories', 'tags']
    logger.info(f"Fetching latest 1000 posts with fields: {fields_to_fetch}")
    
    posts = client.get_published_posts(fields=fields_to_fetch, max_posts=1000)
    client.close()

    if not posts:
        logger.warning("No posts were found. The link map will be empty.")
        link_data = {"posts": []}
    else:
        # Process posts into a structured list
        processed_posts = []
        for post in posts:
            title_rendered = post.get('title', {}).get('rendered', '').strip()
            if not title_rendered or not post.get('link'):
                continue
            
            processed_posts.append({
                "title": title_rendered,
                "link": post['link'],
                "categories": post.get('categories', []),
                "tags": post.get('tags', []),
            })
        
        link_data = {"posts": processed_posts}
        logger.info(f"Successfully processed {len(processed_posts)} posts for the link map.")

    # Save the structured data to the JSON file
    try:
        os.makedirs(OUTPUT_DIR, exist_ok=True)
        with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
            json.dump(link_data, f, ensure_ascii=False, indent=4)
        logger.info(f"Internal link map successfully saved to {OUTPUT_FILE}")
    except IOError as e:
        logger.error(f"Failed to write link map to {OUTPUT_FILE}: {e}")

if __name__ == "__main__":
    build_map()