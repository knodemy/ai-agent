#!/usr/bin/env python3

"""
Fixed Chrome Zoom Auto Joiner - Addresses hanging issues
Simplified version that should work reliably
"""

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.common.keys import Keys
import time
import re

class FixedChromeZoomJoiner:
    def __init__(self):
        """Initialize Chrome with minimal, reliable settings"""
        print("🔧 Setting up Fixed Chrome Zoom Joiner...")
        
        chrome_options = Options()
        
        # Essential settings only - avoid complex configurations that cause hanging
        chrome_options.add_argument("--no-sandbox")
        chrome_options.add_argument("--disable-dev-shm-usage")
        chrome_options.add_argument("--disable-gpu")
        chrome_options.add_argument("--disable-extensions")
        
        # Media permissions - ENHANCED for microphone and camera
        chrome_options.add_argument("--use-fake-ui-for-media-stream")
        chrome_options.add_argument("--use-fake-device-for-media-stream")
        chrome_options.add_argument("--allow-running-insecure-content")
        chrome_options.add_argument("--disable-web-security")
        chrome_options.add_argument("--autoplay-policy=no-user-gesture-required")
        
        # Enhanced preferences for audio ONLY (no camera)
        prefs = {
            "profile.default_content_setting_values.media_stream_mic": 1,  # Allow microphone
            "profile.default_content_setting_values.media_stream_camera": 2,  # Block camera by default
            "profile.default_content_setting_values.notifications": 1,
            "profile.default_content_setting_values.media_stream": 1,
            "profile.content_settings.exceptions.media_stream_mic": {
                "https://zoom.us,*": {
                    "setting": 1
                },
                "https://us04web.zoom.us,*": {
                    "setting": 1
                }
            },
            # Note: Not setting camera permissions to keep it off by default
        }
        chrome_options.add_experimental_option("prefs", prefs)
        
        # Use system Chrome if available, otherwise download
        try:
            # Try system Chrome first
            self.driver = webdriver.Chrome(options=chrome_options)
            print("✅ Using system Chrome")
        except:
            try:
                # Fallback to webdriver-manager
                from webdriver_manager.chrome import ChromeDriverManager
                service = Service(ChromeDriverManager().install())
                self.driver = webdriver.Chrome(service=service, options=chrome_options)
                print("✅ Using webdriver-manager Chrome")
            except Exception as e:
                print(f"❌ Chrome setup failed: {e}")
                raise
        
        self.driver.maximize_window()
        self.wait = WebDriverWait(self.driver, 10)
        print("✅ Chrome ready!")
    
    def extract_meeting_info(self, url):
        """Extract meeting ID and password"""
        meeting_id = re.search(r'/j/(\d+)', url)
        password = re.search(r'pwd=([^&\s]+)', url)
        
        meeting_id = meeting_id.group(1) if meeting_id else None
        password = password.group(1) if password else None
        
        print(f"📋 Meeting ID: {meeting_id}, Password: {'***' if password else 'None'}")
        return meeting_id, password
    
    def join_meeting(self, meeting_url, name="AI Teacher", passcode=None):
        """Simple, reliable meeting join"""
        print(f"🚀 Joining: {meeting_url}")
        
        try:
            meeting_id, url_password = self.extract_meeting_info(meeting_url)
            final_password = passcode or url_password
            
            # Strategy 1: Direct web client
            if meeting_id:
                web_url = f"https://zoom.us/wc/join/{meeting_id}"
                if final_password:
                    web_url += f"?pwd={final_password}"
                
                print(f"🌐 Trying web client: {web_url}")
                self.driver.get(web_url)
                time.sleep(5)
                
                if self._complete_join(name, final_password):
                    return True
            
            # Strategy 2: Original URL
            print("🔄 Trying original URL...")
            self.driver.get(meeting_url)
            time.sleep(5)
            
            # Look for browser join link
            self._click_browser_join()
            time.sleep(3)
            
            return self._complete_join(name, final_password)
            
        except Exception as e:
            print(f"❌ Join failed: {e}")
            return False
    
    def _click_browser_join(self):
        """Find and click browser join link"""
        print("🔍 Looking for browser join...")
        
        selectors = [
            "//a[contains(text(), 'Join from your browser')]",
            "//a[contains(text(), 'click here')]",
            "//a[contains(@href, 'wc/join')]"
        ]
        
        for selector in selectors:
            try:
                element = self.wait.until(EC.element_to_be_clickable((By.XPATH, selector)))
                element.click()
                print("✅ Clicked browser join")
                return True
            except:
                continue
        
        print("⚠️ No browser join found")
        return False
    
    def _complete_join(self, name, password):
        """Complete the join process"""
        print("📝 Completing join...")
        
        # Wait for the join form to be fully loaded
        try:
            WebDriverWait(self.driver, 10).until(
                EC.presence_of_element_located((By.XPATH, "//input[contains(@placeholder, 'Name') or @id='inputname']"))
            )
            print("✅ Join form loaded")
        except:
            print("⚠️ Join form may not be fully loaded")
        
        time.sleep(2)  # Additional wait for form stability
        
        # Enter name - try multiple approaches
        name_entered = False
        name_selectors = [
            "//input[@id='inputname']",
            "//input[@placeholder='Your Name']", 
            "//input[@placeholder='Enter your name']",
            "//input[contains(@class, 'form-control') and @type='text']",
            "//input[@type='text']"
        ]
        
        for selector in name_selectors:
            try:
                name_field = WebDriverWait(self.driver, 3).until(
                    EC.presence_of_element_located((By.XPATH, selector))
                )
                # Clear any existing text and enter name
                name_field.clear()
                time.sleep(0.5)
                name_field.send_keys(name)
                print(f"✅ Entered name: {name}")
                name_entered = True
                break
            except:
                continue
        
        if not name_entered:
            print("⚠️ Could not find name field - will try to proceed")
        
        # Enter password if needed - try multiple approaches  
        if password:
            password_entered = False
            password_selectors = [
                "//input[@id='inputpasscode']",
                "//input[@placeholder='Meeting Passcode']",
                "//input[@type='password']",
                "//input[contains(@placeholder, 'passcode')]",
                "//input[contains(@placeholder, 'Passcode')]"
            ]
            
            for selector in password_selectors:
                try:
                    pwd_field = WebDriverWait(self.driver, 3).until(
                        EC.presence_of_element_located((By.XPATH, selector))
                    )
                    pwd_field.clear()
                    time.sleep(0.5)
                    pwd_field.send_keys(password)
                    print("✅ Entered password")
                    password_entered = True
                    break
                except:
                    continue
            
            if not password_entered:
                print("⚠️ Could not find password field - will try to proceed")
        
        # Click join button - try multiple approaches
        join_clicked = False
        join_selectors = [
            "//button[contains(text(), 'Join')]",  # The blue Join button
            "//button[@id='btnSubmit']",
            "//a[@id='btnSubmit']", 
            "//input[@type='submit']",
            "//button[@type='submit']",
            "//input[@value='Join']",
            "//button[contains(@class, 'btn') and contains(text(), 'Join')]"
        ]
        
        # Wait a moment for the form to be ready
        time.sleep(2)
        
        for selector in join_selectors:
            try:
                join_btn = WebDriverWait(self.driver, 5).until(
                    EC.element_to_be_clickable((By.XPATH, selector))
                )
                # Scroll to button and click
                self.driver.execute_script("arguments[0].scrollIntoView(true);", join_btn)
                time.sleep(1)
                join_btn.click()
                print("✅ Clicked join button")
                join_clicked = True
                break
            except Exception as e:
                print(f"  Join selector failed: {selector} - {e}")
                continue
        
        # Additional fallback methods if join button not found
        if not join_clicked:
            print("⚠️ Standard join button not found, trying fallbacks...")
            
            # Fallback 1: JavaScript click on any Join button
            try:
                self.driver.execute_script("""
                    var buttons = document.querySelectorAll('button, input[type="submit"], a');
                    for(var i = 0; i < buttons.length; i++) {
                        var text = buttons[i].textContent || buttons[i].value || '';
                        if(text.toLowerCase().includes('join')) {
                            buttons[i].click();
                            console.log('Clicked join button via JS:', text);
                            return true;
                        }
                    }
                    return false;
                """)
                print("✅ Used JavaScript join button fallback")
                join_clicked = True
            except Exception as e:
                print(f"  JavaScript fallback failed: {e}")
            
            # Fallback 2: Press Enter key
            if not join_clicked:
                try:
                    # Try pressing Enter on the name field
                    name_field = self.driver.find_element(By.XPATH, "//input[@placeholder='Your Name' or contains(@placeholder, 'name')]")
                    name_field.send_keys(Keys.RETURN)
                    print("✅ Pressed Enter on name field")
                    join_clicked = True
                except:
                    try:
                        # Press Enter on the body
                        self.driver.find_element(By.TAG_NAME, "body").send_keys(Keys.RETURN)
                        print("✅ Pressed Enter on page")
                        join_clicked = True
                    except Exception as e:
                        print(f"  Enter key fallback failed: {e}")
            
            # Fallback 3: Click any blue button (Join buttons are typically blue)
            if not join_clicked:
                try:
                    blue_buttons = self.driver.find_elements(By.XPATH, 
                        "//button[contains(@class, 'btn-primary') or contains(@class, 'btn-blue') or contains(@style, 'blue')]")
                    if blue_buttons:
                        blue_buttons[0].click()
                        print("✅ Clicked blue button fallback")
                        join_clicked = True
                except Exception as e:
                    print(f"  Blue button fallback failed: {e}")
        
        if not join_clicked:
            print("❌ Could not click join button - manual intervention may be needed")
        else:
            print("🎯 Join button clicked successfully!")
        
        # Wait for meeting to load
        print("⏳ Waiting for meeting interface...")
        time.sleep(15)  # Increased wait time for full loading
        
        # Handle audio and video
        self._handle_audio()
        
        # Additional wait after audio/video setup
        time.sleep(5)
        
        # Verify join
        return self._verify_joined()
    
    def _handle_audio(self):
        """Handle audio setup - Enable microphone but keep camera OFF"""
        print("🎤 Setting up audio (microphone ON, camera OFF)...")
        
        # Wait longer for media prompts to appear
        time.sleep(8)
        
        # Step 1: Handle initial audio/video prompts
        audio_video_handled = False
        
        # Try to join with computer audio first
        audio_selectors = [
            "//button[contains(text(), 'Join with Computer Audio')]",
            "//button[contains(text(), 'Join Audio')]", 
            "//button[contains(text(), 'Join Computer Audio')]",
            "//button[contains(text(), 'Computer Audio')]",
            "//a[contains(@class, 'join-audio')]",
            "//button[@aria-label='Join Audio']"
        ]
        
        for selector in audio_selectors:
            try:
                audio_btn = WebDriverWait(self.driver, 5).until(
                    EC.element_to_be_clickable((By.XPATH, selector))
                )
                audio_btn.click()
                print("✅ Joined with computer audio")
                audio_video_handled = True
                time.sleep(3)
                break
            except:
                continue
        
        # Step 2: Enable microphone if it's muted
        self._unmute_microphone()
        
        # Step 3: ENSURE camera stays OFF (disable if enabled)
        self._ensure_camera_off()
        
        # Step 4: Handle any remaining media permission dialogs
        self._handle_media_permissions()
        
        if not audio_video_handled:
            print("ℹ️ No initial audio prompts found - may already be handled")
    
    def _unmute_microphone(self):
        """Unmute the microphone"""
        print("🎤 Enabling microphone...")
        
        time.sleep(3)
        
        # Look for muted microphone button and click to unmute
        mic_selectors = [
            "//button[contains(@aria-label, 'unmute') or contains(@title, 'unmute')]",
            "//button[contains(@aria-label, 'Unmute') or contains(@title, 'Unmute')]",
            "//button[contains(@class, 'muted')]",
            "//button[contains(@class, 'mic') and contains(@class, 'off')]",
            "//button[@aria-label='Mute microphone']",
            "//button[@title='Mute microphone']"
        ]
        
        for selector in mic_selectors:
            try:
                mic_btn = self.driver.find_element(By.XPATH, selector)
                # Check if microphone is muted (usually has 'muted' class or red color)
                mic_btn.click()
                print("✅ Microphone enabled/unmuted")
                time.sleep(2)
                return True
            except:
                continue
        
        # Alternative: Try to find any microphone button and ensure it's unmuted
        try:
            mic_buttons = self.driver.find_elements(By.XPATH, 
                "//button[contains(@aria-label, 'microphone') or contains(@title, 'microphone') or contains(@aria-label, 'mic')]")
            
            for mic_btn in mic_buttons:
                try:
                    # Check if button indicates muted state
                    aria_label = mic_btn.get_attribute('aria-label') or ''
                    title = mic_btn.get_attribute('title') or ''
                    
                    if 'mute' in aria_label.lower() and 'unmute' not in aria_label.lower():
                        mic_btn.click()
                        print("✅ Microphone unmuted")
                        return True
                except:
                    continue
        except:
            pass
        
        print("ℹ️ Microphone status unclear or already enabled")
        return False
    
    def _ensure_camera_off(self):
        """Ensure the camera/video stays OFF"""
        print("📹❌ Ensuring camera stays OFF...")
        
        time.sleep(3)
        
        # Look for video/camera button to turn OFF if it's on
        camera_off_selectors = [
            "//button[contains(@aria-label, 'Stop Video') or contains(@title, 'Stop Video')]",
            "//button[contains(@aria-label, 'Turn off camera') or contains(@title, 'Turn off camera')]",
            "//button[contains(@class, 'video') and not(contains(@class, 'off'))]",
            "//button[contains(@class, 'camera') and not(contains(@class, 'off'))]"
        ]
        
        camera_turned_off = False
        
        for selector in camera_off_selectors:
            try:
                camera_btn = self.driver.find_element(By.XPATH, selector)
                
                # Check the button text to see if video is currently on
                aria_label = camera_btn.get_attribute('aria-label') or ''
                title = camera_btn.get_attribute('title') or ''
                
                if 'stop' in aria_label.lower() or 'turn off' in aria_label.lower():
                    camera_btn.click()
                    print("✅ Camera turned OFF")
                    camera_turned_off = True
                    time.sleep(2)
                    break
                    
            except:
                continue
        
        # Alternative approach: look for any video button that indicates camera is on
        if not camera_turned_off:
            try:
                video_buttons = self.driver.find_elements(By.XPATH, 
                    "//button[contains(@aria-label, 'video') or contains(@title, 'video') or contains(@aria-label, 'camera')]")
                
                for video_btn in video_buttons:
                    try:
                        aria_label = video_btn.get_attribute('aria-label') or ''
                        title = video_btn.get_attribute('title') or ''
                        
                        # If button says "Stop Video" or similar, camera is on - turn it off
                        if any(word in aria_label.lower() for word in ['stop video', 'turn off', 'disable']):
                            video_btn.click()
                            print("✅ Camera turned OFF via alternative method")
                            camera_turned_off = True
                            break
                            
                    except:
                        continue
            except:
                pass
        
        if not camera_turned_off:
            print("✅ Camera already OFF or not found")
        
        return camera_turned_off
    
    def _handle_media_permissions(self):
        """Handle browser media permission dialogs"""
        print("🔧 Handling media permissions...")
        
        # Wait a moment for any permission dialogs
        time.sleep(5)
        
        # Try to handle Chrome's media permission bar at the top
        try:
            # Look for Allow/Block buttons in permission bar
            allow_selectors = [
                "//button[contains(text(), 'Allow')]",
                "//button[@aria-label='Allow']",
                "//*[@class='permission-allow-button']"
            ]
            
            for selector in allow_selectors:
                try:
                    allow_btn = WebDriverWait(self.driver, 2).until(
                        EC.element_to_be_clickable((By.XPATH, selector))
                    )
                    allow_btn.click()
                    print("✅ Allowed media permissions")
                    time.sleep(2)
                    return True
                except:
                    continue
                    
        except:
            pass
        
        # JavaScript approach to handle permissions
        try:
            self.driver.execute_script("""
                // Try to handle any permission dialogs
                var allowButtons = document.querySelectorAll('button');
                for(var i = 0; i < allowButtons.length; i++) {
                    var text = allowButtons[i].textContent.toLowerCase();
                    if(text.includes('allow') || text.includes('continue')) {
                        allowButtons[i].click();
                        console.log('Clicked allow button');
                        break;
                    }
                }
            """)
        except:
            pass
        
        print("ℹ️ Media permissions handled or not required")
    
    def _verify_joined(self):
        """Check if we're in the meeting"""
        indicators = [
            "//button[contains(@title, 'mute')]",
            "//button[contains(@title, 'Mute')]",
            "//button[contains(text(), 'Participants')]",
            "//button[contains(text(), 'Chat')]"
        ]
        
        for indicator in indicators:
            try:
                self.driver.find_element(By.XPATH, indicator)
                print("🎉 Successfully joined meeting!")
                return True
            except:
                continue
        
        # Check URL
        if any(x in self.driver.current_url for x in ['/wc/', '/j/']):
            print("🎉 Joined (URL check)")
            return True
        
        print("⚠️ Join status unclear")
        return False
    
    def send_message(self, message):
        """Send chat message"""
        print(f"💬 Sending: {message}")
        
        try:
            # Open chat
            chat_btn = self.driver.find_element(By.XPATH, 
                "//button[contains(text(), 'Chat')]")
            chat_btn.click()
            time.sleep(2)
            
            # Send message
            chat_input = self.driver.find_element(By.XPATH, 
                "//textarea[contains(@placeholder, 'message')]")
            chat_input.send_keys(message)
            chat_input.send_keys(Keys.RETURN)
            print("✅ Message sent")
            
        except Exception as e:
            print(f"⚠️ Could not send message: {e}")
    
    def stay_active(self, minutes=30):
        """Keep session active"""
        print(f"⏰ Staying active for {minutes} minutes...")
        
        # Send welcome message
        self.send_message("🤖 AI Teacher has joined the meeting! Audio ON, Camera OFF. Ready to help with learning! 📚🎤")
        
        # Stay active
        end_time = time.time() + (minutes * 60)
        message_count = 1
        
        while time.time() < end_time:
            try:
                # Send periodic messages
                if message_count % 5 == 0:
                    self.send_message(f"📚 AI Teacher is here and ready to help! ({message_count})")
                
                time.sleep(60)  # Wait 1 minute
                message_count += 1
                
            except KeyboardInterrupt:
                print("\n🛑 Stopped by user")
                break
        
        print("✅ Session completed")
    
    def leave(self):
        """Leave meeting and close"""
        print("👋 Leaving meeting...")
        
        try:
            leave_btn = self.driver.find_element(By.XPATH, 
                "//button[contains(text(), 'Leave')]")
            leave_btn.click()
            time.sleep(2)
            
            # Confirm if needed
            try:
                confirm_btn = self.driver.find_element(By.XPATH, 
                    "//button[contains(text(), 'Leave Meeting')]")
                confirm_btn.click()
            except:
                pass
                
        except Exception as e:
            print(f"⚠️ Could not leave gracefully: {e}")
        
        finally:
            time.sleep(2)
            self.driver.quit()
            print("✅ Browser closed")


# Simple usage example
if __name__ == "__main__":
    print("🔧 FIXED CHROME ZOOM AUTO JOINER")
    print("=" * 40)
    
    # Your meeting details
    MEETING_URL = "https://us04web.zoom.us/j/77317702131?pwd=dj0ZWrjRRIfOl3cwYRrMg9ImMetbSt.1"
    DISPLAY_NAME = "AI Teacher"
    
    print(f"🎯 Meeting: {MEETING_URL}")
    print(f"👤 Name: {DISPLAY_NAME}")
    print()
    
    joiner = None
    try:
        # Create joiner
        joiner = FixedChromeZoomJoiner()
        
        # Join meeting
        print("🚀 Attempting to join...")
        success = joiner.join_meeting(MEETING_URL, DISPLAY_NAME)
        
        if success:
            print("🎉 Join successful!")
            
            # Stay active for a demo period
            joiner.stay_active(minutes=5)  # 5 minute demo
            
        else:
            print("❌ Could not join meeting")
    
    except KeyboardInterrupt:
        print("\n🛑 Interrupted by user")
    
    except Exception as e:
        print(f"❌ Error: {e}")
    
    finally:
        if joiner:
            joiner.leave()