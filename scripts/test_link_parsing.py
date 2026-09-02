from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'app'))

from downloader import extract_telegram_links, parse_chat_link, parse_message_link  # noqa: E402

cases = {
    'https://t.me/example/42': ('example', 42),
    'http://telegram.me/example/42?single': ('example', 42),
    't.me/@example/42': ('example', 42),
    'https://www.t.me/s/example/42': ('example', 42),
    'https://t.me/c/3328229190/168/378?single': (-1003328229190, 378),
    'https://telegram.me/c/3328229190/168/378': (-1003328229190, 378),
    'tg://resolve?domain=example&post=42': ('example', 42),
    'tg://privatepost?channel=3328229190&post=378': (-1003328229190, 378),
    'tg://msg_url?channel=3328229190&post=378': (-1003328229190, 378),
    'tg://openmessage?chat_id=-1003328229190&message_id=378': (-1003328229190, 378),
}
for link, expected in cases.items():
    assert parse_message_link(link) == expected, (link, parse_message_link(link))

assert parse_chat_link('https://t.me/example') == 'example'
assert parse_chat_link('https://t.me/c/3328229190') == -1003328229190
assert parse_chat_link('https://t.me/+InviteHash').startswith('https://t.me/+')
assert parse_chat_link('tg://join?invite=InviteHash') == 'https://t.me/+InviteHash'
assert parse_message_link('https://t.me/c/4405004978/3') == (-1004405004978, 3)
assert parse_message_link('https://t.me/c/4405004978/3/28?single') == (-1004405004978, 28)
# Regression for the reported internal topic/message URL.
assert parse_message_link('https://t.me/c/4405004978/39/49') == (-1004405004978, 49)
assert parse_message_link('https://t.me/c/4405004978/39/49?single#top') == (-1004405004978, 49)
assert parse_message_link('https://t.me/c/4405004978/39/49،') == (-1004405004978, 49)

text = '''
https://t.me/example/42, t.me/example/42?single
https://t.me/c/3328229190/168/378?single
telegram.me/example/43
TG://resolve?domain=example&post=44
https://telegram.dog/example/45
https://t.me/+InviteHash
'''
assert extract_telegram_links(text) == [
    'https://t.me/example/42',
    'https://t.me/c/3328229190/168/378?single',
    'telegram.me/example/43',
    'TG://resolve?domain=example&post=44',
    'https://telegram.dog/example/45',
    'https://t.me/+InviteHash',
]

assert extract_telegram_links('تم التنزيل من https://t.me/c/4405004978/39/49.') == [
    'https://t.me/c/4405004978/39/49'
]

concatenated = (
    'https://t.me/c/4405004978/3 '
    'https://t.me/c/4405004978/3/28?single'
    'https://t.me/c/4405004978/3/27'
    'https://t.me/c/4405004978/3/26'
    'https://t.me/c/4405004978/3/24'
)
assert extract_telegram_links(concatenated) == [
    'https://t.me/c/4405004978/3',
    'https://t.me/c/4405004978/3/28?single',
    'https://t.me/c/4405004978/3/27',
    'https://t.me/c/4405004978/3/26',
    'https://t.me/c/4405004978/3/24',
]
print('LINK_PARSING_OK')
