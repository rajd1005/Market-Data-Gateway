import os
import json
import time
import threading
import logging
import redis
from kiteconnect import KiteConnect, KiteTicker

# Import your existing modules
import config
import auto_login

# --- CONFIGURATION ---
# Use Railway's Redis URL or default to local
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

# Setup Logging
logging.basicConfig(level=logging.INFO, format='[GATEWAY] %(asctime)s - %(message)s')

class MarketDataGateway:
    def __init__(self):
        self.r = redis.from_url(REDIS_URL, decode_responses=True)
        self.kite = KiteConnect(api_key=config.API_KEY)
        self.kws = None
        self.access_token = None
        self.subscribed_tokens = set()
        
    def perform_login(self):
        """
        Uses the existing auto_login.py logic to get a fresh session.
        """
        logging.info("🔄 Starting Auto-Login Sequence...")
        try:
            # Step 1: Get Request Token via Selenium
            request_token, error = auto_login.perform_auto_login(self.kite)
            
            if not request_token:
                logging.error(f"❌ Login Failed: {error}")
                return False
                
            # Step 2: Generate Access Token
            data = self.kite.generate_session(request_token, api_secret=config.API_SECRET)
            self.access_token = data["access_token"]
            self.kite.set_access_token(self.access_token)
            
            # Step 3: Share Token with the Pool (so Trading Bots can use it for Orders)
            self.r.set("ZERODHA_ACCESS_TOKEN", self.access_token)
            self.r.set("ZERODHA_LOGIN_TIME", time.time())
            
            logging.info("✅ Login Successful! Access Token stored in Redis.")
            return True
            
        except Exception as e:
            logging.error(f"❌ Critical Login Error: {e}")
            return False

    def on_ticks(self, ws, ticks):
        """
        Push Data to 'The Pool' (Redis)
        """
        pipe = self.r.pipeline()
        for tick in ticks:
            token = tick['instrument_token']
            
            # 1. POOLING: Update 'Last Traded Price' Key (Snapshot)
            # Keys are stored as "LTP:256265" -> 22150.5
            if 'last_price' in tick:
                pipe.set(f"LTP:{token}", tick['last_price'])
            
            # 2. STREAMING: Broadcast full tick data (Real-time)
            # Publishes to channel "market_ticks"
            pipe.publish('market_ticks', json.dumps(tick))
            
        pipe.execute()

    def on_connect(self, ws, response):
        logging.info("🔌 Ticker Connected to Zerodha!")
        # Resubscribe to tokens requested by bots
        if self.subscribed_tokens:
            logging.info(f"resubscribing to {len(self.subscribed_tokens)} tokens...")
            ws.subscribe(list(self.subscribed_tokens))
            ws.set_mode(ws.MODE_FULL, list(self.subscribed_tokens))

    def on_close(self, ws, code, reason):
        logging.warning(f"⚠️ Ticker Disconnected: {code} - {reason}")

    def command_listener(self):
        """
        Listens for requests from Trading Bots (e.g., "Subscribe to NIFTY")
        """
        pubsub = self.r.pubsub()
        pubsub.subscribe('gateway_commands')
        logging.info("👂 Listening for bot commands on 'gateway_commands'...")

        for message in pubsub.listen():
            if message['type'] == 'message':
                try:
                    data = json.loads(message['data'])
                    action = data.get('action')
                    
                    if action == 'SUBSCRIBE':
                        tokens = data.get('tokens', [])
                        # Convert to integers and filter duplicates
                        new_tokens = [int(t) for t in tokens if int(t) not in self.subscribed_tokens]
                        
                        if new_tokens and self.kws:
                            logging.info(f"📥 Received Subscribe Request: {new_tokens}")
                            self.kws.subscribe(new_tokens)
                            self.kws.set_mode(self.kws.MODE_FULL, new_tokens)
                            self.subscribed_tokens.update(new_tokens)
                            
                except Exception as e:
                    logging.error(f"Command Error: {e}")

    def start(self):
        # 1. Login Loop
        while True:
            if self.perform_login():
                break
            logging.info("Retrying login in 30 seconds...")
            time.sleep(30)

        # 2. Init Ticker
        self.kws = KiteTicker(config.API_KEY, self.access_token)
        self.kws.on_ticks = self.on_ticks
        self.kws.on_connect = self.on_connect
        self.kws.on_close = self.on_close

        # 3. Start Command Listener (Background Thread)
        t = threading.Thread(target=self.command_listener, daemon=True)
        t.start()

        # 4. Start Ticker (Blocking)
        logging.info("🚀 Gateway is Live. Broadcasting Data...")
        self.kws.connect(threaded=False)

if __name__ == "__main__":
    gateway = MarketDataGateway()
    gateway.start()
