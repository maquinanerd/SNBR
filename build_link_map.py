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
    Fetches latest posts, resolves their tags, and builds a structured link map
    with multiple keywords (title + tags) for each URL.
    """
    logger.info("Initializing WordPress client for link map generation...")
    if not WORDPRESS_CONFIG.get('url'):
        logger.error("WordPress URL not configured. Aborting.")
        return

    client = WordPressClient(WORDPRESS_CONFIG, WORDPRESS_CATEGORIES)
    
    fields_to_fetch = ['id', 'title', 'link', 'categories', 'tags']
    logger.info(f"Fetching latest 1000 posts with fields: {fields_to_fetch}")
    posts = client.get_published_posts(fields=fields_to_fetch, max_posts=1000)

    if not posts:
        logger.warning("No posts were found. The link map will be empty.")
        client.close()
        return

    # Collect all unique tag IDs from all posts
    all_tag_ids = set()
    for post in posts:
        all_tag_ids.update(post.get('tags', []))

    # Fetch names for all collected tag IDs in efficient, batched requests
    logger.info(f"Found {len(all_tag_ids)} unique tag IDs to resolve.")
    tag_id_to_name_map = client.get_tags_map_by_ids(list(all_tag_ids))
    client.close() # Close client after all API calls are done

    # Build the final list of link options with expanded keywords
    processed_posts = []
    for post in posts:
        title = post.get('title', {}).get('rendered', '').strip()
        if not title or not post.get('link'):
            continue
        
        # Start keywords with the post title
        keywords = {title}
        
        # Add tag names as keywords
        post_tag_ids = post.get('tags', [])
        for tag_id in post_tag_ids:
            tag_name = tag_id_to_name_map.get(tag_id)
            if tag_name:
                keywords.add(tag_name)
        
        processed_posts.append({
            "link": post['link'],
            "keywords": list(keywords),
            "categories": post.get('categories', []),
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
