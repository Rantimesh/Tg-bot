import requests
import telegram
import schedule
import time

# Telegram Bot Token
TELEGRAM_TOKEN = "7966679922:AAEaevBL0kPBqjNevm5ghdw_zkRnyQtr_Rs"
CHAT_ID = "-1002811295204"

# Initialize bot
bot = telegram.Bot(token=TELEGRAM_TOKEN)

# Dexscreener API URL
DEXSCREENER_API_URL = "https://api.dexscreener.com/latest/dex/tokens"

# Store posted links to avoid duplicates
posted_links = set()

def fetch_new_tokens():
    response = requests.get(DEXSCREENER_API_URL)
    if response.status_code == 200:
        data = response.json()
        tokens = data.get("pairs", [])

        for token in tokens:
            telegram_link = token.get("telegram")
            if telegram_link and telegram_link not in posted_links:
                message = f"New Token Launched!\nName: {token['baseToken']['name']}\nSymbol: {token['baseToken']['symbol']}\nTelegram: {telegram_link}"
                bot.send_message(chat_id=CHAT_ID, text=message)
                posted_links.add(telegram_link)

def start_bot():
    schedule.every(5).minutes.do(fetch_new_tokens)
    while True:
        schedule.run_pending()
        time.sleep(10)

if __name__ == "__main__":
    start_bot()
