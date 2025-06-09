import requests
import asyncio
import logging
import os
import json
import time
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any
from telegram import Bot
from telegram.error import TelegramError
import schedule
import threading

# Logging configuration
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler('bot.log')
    ]
)
logger = logging.getLogger(__name__)

# Configuration
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "7966679922:AAEaevBL0kPBqjNevm5ghdw_zkRnyQtr_Rs")
CHAT_ID = os.getenv("CHAT_ID", "-1002811295204")

# Bot configuration
CONFIG = {
    "min_market_cap": 10000,     # $10k minimum
    "max_market_cap": 1000000,   # $1M maximum  
    "min_age_seconds": 1,        # 1 second minimum age
    "max_age_seconds": 86400,    # 24 hours maximum age
    "min_liquidity": 500,        # $500 minimum liquidity
    "scan_interval_minutes": 3,   # Scan every 3 minutes
    "max_tokens_per_scan": 50,   # Post max 50 tokens per scan
    "duplicate_check_hours": 6,   # Don't repost within 6 hours
}

class TokenScanner:
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        })

    def get_chain_endpoints(self) -> List[str]:
        """Get DexScreener trending and token endpoints"""
        return [
            "https://api.dexscreener.com/token-profiles/latest/v1",
            "https://api.dexscreener.com/latest/dex/tokens/trending",
            "https://api.dexscreener.com/orders/v1/ethereum",
            "https://api.dexscreener.com/orders/v1/bsc",
            "https://api.dexscreener.com/orders/v1/solana",
            "https://api.dexscreener.com/orders/v1/polygon",
            "https://api.dexscreener.com/orders/v1/arbitrum",
            "https://api.dexscreener.com/orders/v1/base",
        ]

    def fetch_pairs_from_endpoint(self, endpoint: str) -> List[Dict]:
        """Fetch token pairs from DexScreener endpoints"""
        try:
            logger.info(f"Fetching from: {endpoint}")
            response = self.session.get(endpoint, timeout=30)

            if response.status_code == 200:
                data = response.json()
                
                # Handle different response structures
                pairs = []
                if 'pairs' in data:
                    pairs = data['pairs']
                elif 'data' in data and isinstance(data['data'], list):
                    pairs = data['data']
                elif isinstance(data, list):
                    pairs = data
                elif 'tokens' in data:
                    # Convert token data to pair-like structure
                    for token in data['tokens']:
                        if 'pairs' in token:
                            pairs.extend(token['pairs'])

                if not pairs:
                    logger.warning(f"No pairs returned from {endpoint}")
                    return []

                # Filter pairs by age and structure
                recent_pairs = []
                current_time = datetime.now()
                
                for pair in pairs:
                    if not self.is_valid_pair_structure(pair):
                        continue
                        
                    # Check age requirement
                    pair_created_at = pair.get('pairCreatedAt')
                    if pair_created_at:
                        created_time = datetime.fromtimestamp(pair_created_at / 1000)
                        age_seconds = (current_time - created_time).total_seconds()
                        
                        # Only include recent pairs (within max age)
                        if age_seconds <= CONFIG["max_age_seconds"]:
                            recent_pairs.append(pair)

                # Sort by creation time (newest first)
                recent_pairs.sort(key=lambda x: x.get('pairCreatedAt', 0), reverse=True)

                logger.info(f"Got {len(recent_pairs)} recent pairs from {endpoint}")
                return recent_pairs[:200]

            else:
                logger.error(f"API error {response.status_code} for {endpoint}: {response.text}")
                return []

        except Exception as e:
            logger.error(f"Error fetching from {endpoint}: {e}")
            return []

    

    def is_valid_pair_structure(self, pair: Dict) -> bool:
        """Check if pair has required structure"""
        try:
            base_token = pair.get('baseToken')
            if not base_token:
                return False

            required_fields = ['address', 'name', 'symbol']
            for field in required_fields:
                if not base_token.get(field):
                    return False

            # Must have price and liquidity data
            if not pair.get('priceUsd') or not pair.get('liquidity', {}).get('usd'):
                return False

            return True

        except Exception:
            return False

    def scan_all_chains(self) -> List[Dict]:
        """Scan all supported chains for new tokens"""
        all_pairs = []
        
        # Main endpoints
        endpoints = self.get_chain_endpoints()
        
        # Additional search endpoints for more coverage
        search_endpoints = [
            "https://api.dexscreener.com/latest/dex/search/?q=WETH",
            "https://api.dexscreener.com/latest/dex/search/?q=PEPE",
            "https://api.dexscreener.com/latest/dex/search/?q=AI",
            "https://api.dexscreener.com/latest/dex/search/?q=MEME",
            "https://api.dexscreener.com/latest/dex/search/?q=BASE",
        ]
        
        all_endpoints = endpoints + search_endpoints
        logger.info(f"Scanning {len(all_endpoints)} endpoints...")

        for endpoint in all_endpoints:
            pairs = self.fetch_pairs_from_endpoint(endpoint)
            all_pairs.extend(pairs)
            time.sleep(1)  # Rate limiting

        # Remove duplicates by address
        seen_addresses = set()
        unique_pairs = []

        for pair in all_pairs:
            address = pair.get('baseToken', {}).get('address')
            if address and address not in seen_addresses:
                seen_addresses.add(address)
                unique_pairs.append(pair)

        logger.info(f"Found {len(unique_pairs)} unique tokens")
        return unique_pairs

class TokenFilter:
    @staticmethod
    def passes_criteria(pair: Dict) -> bool:
        """Check if token meets all criteria"""
        try:
            # Market cap check
            market_cap = pair.get('marketCap')
            if not market_cap:
                return False

            if not (CONFIG["min_market_cap"] <= market_cap <= CONFIG["max_market_cap"]):
                return False

            # Age check (1 second to 7 days)
            pair_created_at = pair.get('pairCreatedAt')
            if not pair_created_at:
                return False

            created_time = datetime.fromtimestamp(pair_created_at / 1000)
            age_seconds = (datetime.now() - created_time).total_seconds()

            if not (CONFIG["min_age_seconds"] <= age_seconds <= CONFIG["max_age_seconds"]):
                return False

            # Liquidity check
            liquidity = pair.get('liquidity', {}).get('usd', 0)
            if liquidity < CONFIG["min_liquidity"]:
                return False

            # Social presence is preferred but not required for more tokens
            return True

        except Exception as e:
            logger.error(f"Error filtering token: {e}")
            return False

    @staticmethod
    def has_social_presence(pair: Dict) -> bool:
        """Check if token has social media presence"""
        try:
            info = pair.get('info', {})

            # Check for website
            if info.get('website'):
                return True

            # Check for social links
            socials = info.get('socials', [])
            if socials and len(socials) > 0:
                return True

            return False

        except Exception:
            return False

    @staticmethod
    def extract_social_links(pair: Dict) -> Dict[str, str]:
        """Extract social media links"""
        links = {
            'website': '',
            'telegram': '',
            'twitter': '',
            'discord': ''
        }

        try:
            info = pair.get('info', {})

            # Website
            if info.get('website'):
                links['website'] = info['website']

            # Social links
            socials = info.get('socials', [])
            for social in socials:
                url = social.get('url', '').lower()
                type_field = social.get('type', '').lower()

                if 'telegram' in url or 't.me' in url or type_field == 'telegram':
                    links['telegram'] = social.get('url', '')
                elif 'twitter' in url or 'x.com' in url or type_field == 'twitter':
                    links['twitter'] = social.get('url', '')
                elif 'discord' in url or type_field == 'discord':
                    links['discord'] = social.get('url', '')

        except Exception as e:
            logger.error(f"Error extracting social links: {e}")

        return links

class MessageFormatter:
    @staticmethod
    def format_token_message(pair: Dict) -> str:
        """Format token data into Telegram message"""
        try:
            base_token = pair['baseToken']
            name = base_token['name']
            symbol = base_token['symbol']
            address = base_token['address']

            # Market data
            price = float(pair.get('priceUsd', 0))
            market_cap = pair.get('marketCap', 0)
            liquidity = pair.get('liquidity', {}).get('usd', 0)
            volume_24h = pair.get('volume', {}).get('h24', 0)
            price_change = pair.get('priceChange', {}).get('h24', 0)

            # Chain info
            chain_id = pair.get('chainId', 'unknown')
            chain_names = {
                'ethereum': '🔷 Ethereum',
                'solana': '🌅 Solana',
                'bsc': '🟡 BSC', 
                'polygon': '🟣 Polygon',
                'arbitrum': '🔵 Arbitrum'
            }
            chain = chain_names.get(chain_id, f'⛓️ {chain_id.title()}')

            # Calculate age
            pair_created_at = pair.get('pairCreatedAt')
            created_time = datetime.fromtimestamp(pair_created_at / 1000)
            age_hours = int((datetime.now() - created_time).total_seconds() / 3600)
            age_display = f"{age_hours}h" if age_hours < 24 else f"{age_hours//24}d {age_hours%24}h"

            # Format numbers
            def format_number(num):
                if num >= 1e6:
                    return f"${num/1e6:.2f}M"
                elif num >= 1e3:
                    return f"${num/1e3:.1f}K"
                else:
                    return f"${num:.2f}"

            # Price change emoji
            change_emoji = "🟢" if price_change > 0 else "🔴" if price_change < -5 else "🟡"

            # Short address
            short_address = f"{address[:6]}...{address[-4:]}"

            # Build message
            message = f"""🚀 **NEW TOKEN ALERT** 🚀

💎 **{name} (${symbol})**
{chain} • Age: {age_display}

📊 **Market Data:**
💰 Price: ${price:.8f}
{change_emoji} 24h Change: {price_change:.2f}%
📈 Market Cap: {format_number(market_cap)}
💧 Liquidity: {format_number(liquidity)}
📊 Volume 24h: {format_number(volume_24h)}

🔗 **Contract:** `{short_address}`

🌐 **Links:**"""

            # Add social links
            social_links = TokenFilter.extract_social_links(pair)

            if social_links['website']:
                message += f"\n🌍 [Website]({social_links['website']})"
            if social_links['telegram']:
                message += f"\n📱 [Telegram]({social_links['telegram']})"
            if social_links['twitter']:
                message += f"\n🐦 [Twitter]({social_links['twitter']})"
            if social_links['discord']:
                message += f"\n💬 [Discord]({social_links['discord']})"

            # Chart link
            dexscreener_url = f"https://dexscreener.com/{chain_id}/{address}"
            message += f"\n📊 [Chart]({dexscreener_url})"

            # Trading links
            if chain_id == 'ethereum':
                trade_url = f"https://app.uniswap.org/#/swap?inputCurrency=ETH&outputCurrency={address}"
                message += f"\n🦄 [Trade on Uniswap]({trade_url})"
            elif chain_id == 'bsc':
                trade_url = f"https://pancakeswap.finance/swap?inputCurrency=BNB&outputCurrency={address}"
                message += f"\n🥞 [Trade on PancakeSwap]({trade_url})"
            elif chain_id == 'solana':
                trade_url = f"https://jup.ag/swap/SOL-{address}"
                message += f"\n🪐 [Trade on Jupiter]({trade_url})"
            elif chain_id == 'polygon':
                trade_url = f"https://quickswap.exchange/#/swap?inputCurrency=ETH&outputCurrency={address}"
                message += f"\n⚡ [Trade on QuickSwap]({trade_url})"

            message += f"\n\n⚠️ **DYOR - Always verify before investing!**"

            return message

        except Exception as e:
            logger.error(f"Error formatting message: {e}")
            return None

class TelegramBot:
    def __init__(self, token: str, chat_id: str):
        self.bot = Bot(token=token)
        self.chat_id = chat_id
        self.posted_tokens = {}

    async def test_connection(self) -> bool:
        """Test bot connection"""
        try:
            bot_info = await self.bot.get_me()
            logger.info(f"Bot connected: @{bot_info.username}")
            return True
        except Exception as e:
            logger.error(f"Bot connection failed: {e}")
            return False

    async def send_message(self, message: str) -> bool:
        """Send message to Telegram"""
        try:
            await self.bot.send_message(
                chat_id=self.chat_id,
                text=message,
                parse_mode='Markdown',
                disable_web_page_preview=True
            )
            return True
        except Exception as e:
            logger.error(f"Error sending message: {e}")
            return False

    def is_recently_posted(self, token_address: str) -> bool:
        """Check if token was recently posted"""
        if token_address not in self.posted_tokens:
            return False

        posted_time = self.posted_tokens[token_address]
        time_diff = datetime.now() - posted_time
        return time_diff.total_seconds() < CONFIG["duplicate_check_hours"] * 3600

    def mark_as_posted(self, token_address: str):
        """Mark token as posted"""
        self.posted_tokens[token_address] = datetime.now()

class TokenMonitorBot:
    def __init__(self):
        self.telegram_bot = TelegramBot(TELEGRAM_TOKEN, CHAT_ID)
        self.scanner = TokenScanner()
        self.running = False

    async def scan_and_post_tokens(self):
        """Main scanning and posting logic"""
        try:
            logger.info("🔍 Starting token scan...")

            # Scan all chains
            all_pairs = self.scanner.scan_all_chains()

            # Filter valid tokens
            valid_tokens = []
            for pair in all_pairs:
                if TokenFilter.passes_criteria(pair):
                    token_address = pair['baseToken']['address']
                    if not self.telegram_bot.is_recently_posted(token_address):
                        valid_tokens.append(pair)

            logger.info(f"Found {len(valid_tokens)} new valid tokens")

            # Post tokens (limited per scan)
            posted_count = 0
            for token in valid_tokens[:CONFIG["max_tokens_per_scan"]]:
                message = MessageFormatter.format_token_message(token)
                if message:
                    success = await self.telegram_bot.send_message(message)
                    if success:
                        token_address = token['baseToken']['address']
                        self.telegram_bot.mark_as_posted(token_address)
                        posted_count += 1
                        logger.info(f"✅ Posted: {token['baseToken']['name']}")
                        await asyncio.sleep(3)  # Rate limiting
                    else:
                        await asyncio.sleep(5)

            logger.info(f"📤 Posted {posted_count} tokens")

        except Exception as e:
            logger.error(f"Error in scan and post: {e}")

    async def start(self):
        """Start the monitoring bot"""
        logger.info("🤖 Starting Token Monitor Bot...")

        # Test connection
        if not await self.telegram_bot.test_connection():
            logger.error("❌ Failed to connect to Telegram!")
            return

        # Send startup message
        startup_msg = "🤖 **Token Monitor Bot Started!**\n\n✅ Connected to Telegram\n🔍 Scanning multiple chains\n📊 Filtering tokens by criteria\n⚡ Real-time alerts active"
        await self.telegram_bot.send_message(startup_msg)

        self.running = True

        # Initial scan
        await self.scan_and_post_tokens()

        # Main monitoring loop
        while self.running:
            try:
                await asyncio.sleep(CONFIG["scan_interval_minutes"] * 60)
                await self.scan_and_post_tokens()

            except KeyboardInterrupt:
                logger.info("🛑 Bot stopped by user")
                break
            except Exception as e:
                logger.error(f"Error in main loop: {e}")
                await asyncio.sleep(60)

async def main():
    """Main entry point"""
    bot = TokenMonitorBot()
    await bot.start()

if __name__ == "__main__":
    asyncio.run(main())
