import logging
import re
from typing import Dict, List, Set, Any
from bs4 import BeautifulSoup
from app.config import PILAR_POSTS

logger = logging.getLogger(__name__)

EXCLUDED_TAGS = ['a', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'blockquote', 'code', 'pre', 'figure', 'figcaption']

def add_internal_links(
    html_content: str, 
    link_map_data: Dict[str, List[Dict[str, Any]]],
    current_post_categories: List[int] = None,
    max_links: int = 6
) -> str:
    """
    Analyzes HTML and inserts internal links based on a prioritized strategy.

    Args:
        html_content: The HTML string to process.
        link_map_data: The structured link map data from internal_links.json.
        current_post_categories: A list of category IDs for the post being processed.
        max_links: The maximum number of internal links to add.

    Returns:
        The modified HTML string with internal links.
    """
    if not html_content or not link_map_data or not link_map_data.get('posts'):
        return html_content

    soup = BeautifulSoup(html_content, 'html.parser')
    links_inserted = 0
    used_urls: Set[str] = set()
    
    all_link_options = link_map_data['posts']

    # --- Prioritization Logic ---
    pilar_options = []
    category_options = []
    other_options = []

    current_cat_set = set(current_post_categories or [])

    for post_data in all_link_options:
        is_pilar = post_data['link'] in PILAR_POSTS
        shares_category = current_cat_set and not current_cat_set.isdisjoint(post_data.get('categories', []))

        if is_pilar:
            pilar_options.append(post_data)
        elif shares_category:
            category_options.append(post_data)
        else:
            other_options.append(post_data)

    # Sort each list by title length (desc) to match longer phrases first
    sort_key = lambda p: len(p.get('title', ''))
    pilar_options.sort(key=sort_key, reverse=True)
    category_options.sort(key=sort_key, reverse=True)
    other_options.sort(key=sort_key, reverse=True)

    # Combine into a single prioritized list of posts to try
    prioritized_link_options = pilar_options + category_options + other_options

    text_nodes = soup.body.find_all(string=True)

    for node in text_nodes:
        if links_inserted >= max_links:
            break

        if any(node.find_parent(tag) for tag in EXCLUDED_TAGS):
            continue

        original_text = str(node)

        for link_option in prioritized_link_options:
            if links_inserted >= max_links:
                break

            keyword = link_option['title']
            url = link_option['link']

            if url in used_urls:
                continue

            # Regex for case-insensitive, whole-word match
            pattern = re.compile(r'\b(' + re.escape(keyword) + r')\b', re.IGNORECASE)
            
            if pattern.search(original_text):
                link_tag_str = f'<a href="{url}">{keyword}</a>'
                new_html = pattern.sub(link_tag_str, original_text, count=1)
                
                node.replace_with(BeautifulSoup(new_html, 'html.parser'))
                
                links_inserted += 1
                used_urls.add(url)
                
                priority = "PILAR"
                if link_option in category_options:
                    priority = "CATEGORY"
                elif link_option in other_options:
                    priority = "OTHER"
                logger.info(f"Inserted link for '{keyword}' (Priority: {priority})")
                break # Move to the next text node

    return str(soup)