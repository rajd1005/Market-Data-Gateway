import time
import os
import pyotp
from urllib.parse import parse_qs, urlparse
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager
import config

def perform_auto_login(kite_instance):
    print("🔄 Starting Auto-Login Sequence...")
    
    # --- CONFIGURE CHROME OPTIONS FOR RAILWAY/DOCKER ---
    chrome_options = Options()
    chrome_options.add_argument("--headless=new") 
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--disable-gpu")
    chrome_options.add_argument("--window-size=1920,1080")
    chrome_options.add_argument("--disable-notifications")
    chrome_options.add_argument("--ignore-certificate-errors")
    
    # ANTI-BOT DETECTION
    chrome_options.add_argument("--disable-blink-features=AutomationControlled")
    chrome_options.add_experimental_option("excludeSwitches", ["enable-automation"])
    chrome_options.add_experimental_option('useAutomationExtension', False)
    chrome_options.add_argument("--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36")
    
    driver = None
    try:
        # Install/Update Driver automatically
        service = Service(ChromeDriverManager().install())
        driver = webdriver.Chrome(service=service, options=chrome_options)
        
        # Mask WebDriver property
        driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {
            "source": """
                Object.defineProperty(navigator, 'webdriver', {
                    get: () => undefined
                })
            """
        })
        
        login_url = kite_instance.login_url()
        print(f"➡️ Navigating to Zerodha Login...")
        driver.get(login_url)
        wait = WebDriverWait(driver, 30)

        # --- STEP 1: USER ID ---
        print("➡️ Step 1: Entering User ID...")
        try:
            user_id_field = wait.until(EC.element_to_be_clickable((By.ID, "userid")))
            user_id_field.clear()
            user_id_field.send_keys(config.ZERODHA_USER_ID)
            user_id_field.send_keys(Keys.ENTER)
            time.sleep(1.5)
        except Exception as e:
            return None, f"Failed at User ID Step: {str(e)}"

        # --- STEP 2: PASSWORD ---
        print("➡️ Step 2: Entering Password...")
        try:
            password_field = wait.until(EC.element_to_be_clickable((By.ID, "password")))
            password_field.clear()
            password_field.send_keys(config.ZERODHA_PASSWORD)
            password_field.send_keys(Keys.ENTER)
            time.sleep(2)
        except Exception as e:
            return None, f"Failed at Password Step: {str(e)}"

        # --- STEP 3: TOTP ---
        print("➡️ Step 3: Handling TOTP...")
        try:
            # Check for immediate errors
            try:
                error_msg = driver.find_elements(By.CSS_SELECTOR, ".su-message.error, .error-message")
                if error_msg and error_msg[0].is_displayed():
                    return None, f"Login Error: {error_msg[0].text}"
            except: pass

            totp_input = wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, "input[type='text'], input[type='number'], input[placeholder='TOTP']")))
            
            if not config.TOTP_SECRET:
                return None, "TOTP_SECRET is missing in config."
                
            totp_now = pyotp.TOTP(config.TOTP_SECRET).now()
            print(f"   🔑 Entered TOTP")
            
            totp_input.click()
            totp_input.clear()
            totp_input.send_keys(totp_now)
            totp_input.send_keys(Keys.ENTER)
            time.sleep(2)
            
        except Exception as e:
            if "App Code" in driver.page_source:
                return None, "Error: Zerodha asked for Mobile App Code (Not TOTP)."
            return None, f"Failed at TOTP Step: {str(e)}"

        # --- STEP 4: VERIFY SUCCESS (URL CHECK) ---
        print("⏳ Waiting for Redirect to rdmdg.up.railway.app...")
        
        start_time = time.time()
        while time.time() - start_time < 30:
            current_url = driver.current_url
            
            # This works for ANY domain (localhost or railway.app)
            if "request_token=" in current_url:
                parsed = urlparse(current_url)
                request_token = parse_qs(parsed.query).get('request_token', [None])[0]
                if request_token:
                    print(f"✅ Success! Token Captured.")
                    return request_token, None
            
            # Error Check
            if "Incorrect password" in driver.page_source or "Invalid TOTP" in driver.page_source:
                return None, "Login Failed: Invalid Credentials."

            time.sleep(0.5)

        return None, "Login Timed Out. Redirect URL not found."

    except Exception as e:
        print(f"❌ Critical Selenium Error: {e}")
        return None, str(e)
        
    finally:
        if driver:
            try:
                driver.quit()
            except: pass
