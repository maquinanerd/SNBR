from bs4 import BeautifulSoup

def clean_html_for_globo_esporte(soup: BeautifulSoup) -> BeautifulSoup:
    """
    Limpa o HTML de uma página do Globo Esporte, removendo elementos indesejados
    antes da extração principal de conteúdo.
    """
    # Remove os players de vídeo, que contêm imagens de thumbnail
    video_players = soup.find_all('div', class_='video-player')
    for player in video_players:
        player.decompose()
        
    return soup
