import os
import json
import time
import threading
import logging
import redis
from flask import Flask
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

# --- 1. WEB SERVER (Required for Railway Domain) ---
app = Flask(__name__)

@app.route('/')
def home():
    return "<h3>✅ Market Data Gateway is Running</h3><p>Status: Listening for Ticks</p>"

def run_web_server():
    # Railway provides the PORT variable. We must listen on it.
    port = int(os.environ.get("PORT", 8080))
    logging.info(f"🌍 Starting Web Server on Port {port}...")
    app.run(host='0.0.0.0', port=port, debug=False, use_reloader=False)

# --- 2. MARKET DATA LOGIC ---
class MarketDataGateway:
    def __init__(self):
        self.r = redis.from_url(REDIS_URL, decode_responses=True)
        self.kite = KiteConnect(api_key=config.API_KEY)
        self.kws = None
        self.access_token = None
        self.subscribed_tokens = set()
        
    def perform_login(self):
        """
        Uses auto_login.py to capture the token.
        Selenium will browse to https://rdmdg.up.railway.app?request_token=...
        and grab the token from the address bar.
        """
        logging.info("🔄 Starting Auto-Login Sequence...")
        try:
            # Pass the kite instance to your auto_login script
            request_token, error = auto_login.perform_auto_login(self.kite)
            
            if not request_token:
                logging.error(f"❌ Login Failed: {error}")
                return False
                
            # Generate Access Token
            data = self.kite.generate_session(request_token, api_secret=config.API_SECRET)
            self.access_token = data["access_token"]
            self.kite.set_access_token(self.access_token)
            
            # Save Token to Redis for other apps
            self.r.set("ZERODHA_ACCESS_TOKEN", self.access_token)
            logging.info("✅ Login Successful! Access Token ready.")
            return True
            
        except Exception as e:
            logging.error(f"❌ Critical Login Error: {e}")
            return False

    def on_ticks(self, ws, ticks):
        pipe = self.r.pipeline()
        for tick in ticks:
            token = tick['instrument_token']
            
            # POOLING: Save LTP for on-demand fetching
            if 'last_price' in tick:
                pipe.set(f"LTP:{token}", tick['last_price'])
            
            # BROADCAST: Stream to subscribers
            pipe.publish('market_ticks', json.dumps(tick))
            
        pipe.execute()

    def on_connect(self, ws, response):
        logging.info("🔌 Gateway Connected to Zerodha!")
        if self.subscribed_tokens:
            ws.subscribe(list(self.subscribed_tokens))
            ws.set_mode(ws.MODE_FULL, list(self.subscribed_tokens))

    def on_close(self, ws, code, reason):
        logging.warning(f"⚠️ Gateway Disconnected: {code} - {reason}")

    def command_listener(self):
        """
        Listens for SUBSCRIBE commands from your trading bots
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
                        new_tokens = [int(t) for t in tokens if int(t) not in self.subscribed_tokens]
                        
                        if new_tokens and self.kws:
                            logging.info(f"📥 Received Request to Watch: {new_tokens}")
                            self.kws.subscribe(new_tokens)
                            self.kws.set_mode(self.kws.MODE_FULL, new_tokens)
                            self.subscribed_tokens.update(new_tokens)
                            
                except Exception as e:
                    logging.error(f"Command Error: {e}")

    def start(self):
        # 1. Login Logic
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

        # 3. Start Command Listener
        t = threading.Thread(target=self.command_listener, daemon=True)
        t.start()

        # 4. Start Ticker (Blocking)
        logging.info("🚀 Gateway Logic Started.")
        self.kws.connect(threaded=False)

if __name__ == "__main__":
    # A. Start the Dummy Web Server (Required for rdmdg.up.railway.app)
    web_thread = threading.Thread(target=run_web_server, daemon=True)
    web_thread.start()

    # B. Start the Gateway Engine
    gateway = MarketDataGateway()
    gateway.start()
