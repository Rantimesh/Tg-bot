import os
import requests
import sqlite3
from telegram import Bot, Update
from telegram.ext import Application, CommandHandler, ContextTypes
from dotenv import load_dotenv
from flask import Flask, request, jsonify
import asyncio

# Load environment variables
load_dotenv()
TOKEN = os.getenv('TELEGRAM_TOKEN')

# Initialize database
conn = sqlite3.connect('tokens.db', check_same_thread=False) # Important for Flask!
c = conn.cursor()
c.execute('''CREATE TABLE IF NOT EXISTS seen_tokens 
             (token_address TEXT PRIMARY KEY)''')
conn.commit()

# Flask App
app = Flask(__name__)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🚀 Dex Alert Bot Active!\n"
        "I'll notify you of new tokens with Telegram links every minute."
    )

async def check_new_tokens(context: ContextTypes.DEFAULT_TYPE):
    url = "https://api.dexscreener.com/token-profiles/latest/v1"
    
    try:
        response = requests.get(url)
        response.raise_for_status()
        
        new_alerts = []
        for token in response.json().get('tokenProfiles', []):
            token_address = token.get('address')
            
            # Check if already processed
            c.execute('SELECT 1 FROM seen_tokens WHERE token_address=?', (token_address,))
            if c.fetchone():
                continue
            
            # Find Telegram links
            telegram_links = [
                link for link in token.get('links', {}).values() 
                if 't.me/' in link
            ]
            
            if telegram_links:
                message = (
                    f"New Token Detected 🚀\n"
                    f"Name: {token.get('name', 'N/A')}\n"
                    f"Symbol: {token.get('symbol', 'N/A')}\n"
                    f"Chain: {token.get('chain', 'N/A')}\n"
                    f"Telegram: {telegram_links[0]}\n"
                    f"Chart: {token.get('url', '')}"
                )
                new_alerts.append(message)
                c.execute('INSERT INTO seen_tokens VALUES (?)', (token_address,))
        
        conn.commit()
        
        # Send alerts
        if new_alerts:
            for chat_id in context.bot_data.get('subscribed_chats', []):
                for alert in new_alerts:
                    await context.bot.send_message(
                        chat_id=chat_id,
                        text=alert,
                        disable_web_page_preview=True
                    )
    
    except Exception as e:
        print(f"Error: {e}")

# Health check route (important for Render)
@app.route('/health')
def health_check():
    return jsonify({"status": "ok"}), 200

@app.route('/', methods=['POST', 'GET']) # Add this route
def webhook_handler():
    if request.method == "POST":
        update = Update.de_json(request.get_json(force=True), application.bot)
        application.process_update(update)
    return 'ok'

async def main():
    global application
    application = Application.builder().token(TOKEN).build()
    
    # Add handlers
    application.add_handler(CommandHandler('start', start))
    
    # Set up periodic checks
    job_queue = application.job_queue
    job_queue.run_repeating(check_new_tokens, interval=60, first=10)
    
    try:
        await application.initialize()
        await application.start()
        await application.updater.start_polling()
        await application.idle()
    except Exception as e:
        print(f"Error during bot startup: {e}")

if __name__ == '__main__':
    asyncio.run(main())
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
