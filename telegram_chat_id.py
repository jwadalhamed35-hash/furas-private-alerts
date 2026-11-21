"""Local setup helper. Token is entered privately, never passed on command line."""
from getpass import getpass
import requests

def main():
    token = getpass('Telegram bot token (hidden): ').strip()
    try:
        response = requests.get(f'https://api.telegram.org/bot{token}/getUpdates', timeout=30)
        response.raise_for_status()
        data = response.json()
        if data.get('ok') is not True:
            raise ValueError()
        chats = {}
        for update in data.get('result', []):
            for kind in ('message', 'channel_post', 'my_chat_member'):
                chat = update.get(kind, {}).get('chat')
                if chat:
                    chats[chat['id']] = chat.get('title') or chat.get('username') or chat.get('first_name', '')
        for identity, title in chats.items():
            print(f'{identity}: {title}')
        if not chats:
            print('No chats found. Send /start@your_bot in the group, then retry.')
    except (requests.RequestException, ValueError, KeyError):
        print('Could not read updates. Check token, connection and whether another service uses this bot.')
        raise SystemExit(1)

if __name__ == '__main__':
    main()
