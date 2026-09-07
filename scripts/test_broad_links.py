import sys
from urllib.parse import quote

sys.path.insert(0, "app")
from downloader import extract_telegram_links, parse_message_link

cases = {
    "شاهد https://t.me/c/4405004978/3/20 الآن": "https://t.me/c/4405004978/3/20",
    "t.me / c / 4405004978 / 3 / 20": "t.me/c/4405004978/3/20",
    "https://telegram.me/channel/7، ثم t.me/+InviteCode": "https://telegram.me/channel/7",
    "رابط مشفر: " + quote("https://t.me/channel/9"): "https://t.me/channel/9",
    "tg://resolve?domain=channel&post=7": "tg://resolve?domain=channel&post=7",
}

for text, expected in cases.items():
    links = extract_telegram_links(text)
    assert links and links[0] == expected, (text, links)

assert parse_message_link("https://t.me/c/4405004978/3/20") == (-1004405004978, 20)
print("BROAD_LINKS_OK")
