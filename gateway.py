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
REDIS_URL = os.getenv("REDIS_URL")
if not REDIS_URL:
    raise ValueError("❌ REDIS_URL is missing! Check Railway Variables.")

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
        Uses your existing auto_login.py to get a fresh session.
        """
        logging.info("🔄 Starting Auto-Login Sequence...")
        try:
            # Step 1: Get Request Token via Selenium
            # passing self.kite is standard for your auto_login script
            request_token, error = auto_login.perform_auto_login(self.kite)
            
            if not request_token:
                logging.error(f"❌ Login Failed: {error}")
                return False
                
            # Step 2: Generate Access Token
            data = self.kite.generate_session(request_token, api_secret=config.API_SECRET)
            self.access_token = data["access_token"]
            self.kite.set_access_token(self.access_token)
            
            # Step 3: Share Token with the Pool (Optional: for future Order Execution)
            self.r.set("ZERODHA_ACCESS_TOKEN", self.access_token)
            logging.info("✅ Login Successful! Access Token ready.")
            return True
            
        except Exception as e:
            logging.error(f"❌ Critical Login Error: {e}")
            return False

    def on_ticks(self, ws, ticks):
        """
        THE POOLING METHOD:
        Takes data from Zerodha and saves it to Redis so others can pick it up.
        """
        pipe = self.r.pipeline()
        for tick in ticks:
            token = tick['instrument_token']
            
            # 1. POOLING (The "Fetch" Method)
            # We save the Last Traded Price (LTP) to a specific key.
            # Any system can ask Redis: "What is LTP:256265?" and get the answer instantly.
            if 'last_price' in tick:
                pipe.set(f"LTP:{token}", tick['last_price'])
            
            # 2. FULL DATA (Optional)
            # We can also save the full JSON if needed
            pipe.set(f"FULL:{token}", json.dumps(tick))
            
            # 3. STREAMING (The "Push" Method)
            # We also shout it out to anyone listening
            pipe.publish('market_ticks', json.dumps(tick))
            
        pipe.execute()

    def on_connect(self, ws, response):
        logging.info("🔌 Gateway Connected to Zerodha Ticker!")
        # If the gateway restarts, re-subscribe to what we need
        if self.subscribed_tokens:
            ws.subscribe(list(self.subscribed_tokens))
            ws.set_mode(ws.MODE_FULL, list(self.subscribed_tokens))

    def on_close(self, ws, code, reason):
        logging.warning(f"⚠️ Gateway Disconnected: {code} - {reason}")

    def command_listener(self):
        """
        Listens for 'SUBSCRIBE' commands from your future systems.
        """
        pubsub = self.r.pubsub()
        pubsub.subscribe('gateway_commands')
        logging.info("👂 Listening for Subscription Commands...")

        for message in pubsub.listen():
            if message['type'] == 'message':
                try:
                    data = json.loads(message['data'])
                    action = data.get('action')
                    
                    if action == 'SUBSCRIBE':
                        tokens = data.get('tokens', [])
                        # Clean and filter tokens
                        new_tokens = [int(t) for t in tokens if int(t) not in self.subscribed_tokens]
                        
                        if new_tokens and self.kws:
                            logging.info(f"📥 Received Request to Watch: {new_tokens}")
                            self.kws.subscribe(new_tokens)
                            self.kws.set_mode(self.kws.MODE_FULL, new_tokens)
                            self.subscribed_tokens.update(new_tokens)
                            
                except Exception as e:
                    logging.error(f"Command Error: {e}")

    def start(self):
        # 1. Login
        while True:
            if self.perform_login():
                break
            logging.info("Retrying login in 30 seconds...")
            time.sleep(30)

        # 2. Setup Ticker
        self.kws = KiteTicker(config.API_KEY, self.access_token)
        self.kws.on_ticks = self.on_ticks
        self.kws.on_connect = self.on_connect
        self.kws.on_close = self.on_close

        # 3. Start Command Listener (Background)
        t = threading.Thread(target=self.command_listener, daemon=True)
        t.start()

        # 4. Start Ticker (Blocking)
        logging.info("🚀 Gateway Started via Pooling Method.")
        self.kws.connect(threaded=False)

if __name__ == "__main__":
    gateway = MarketDataGateway()
    gateway.start()
