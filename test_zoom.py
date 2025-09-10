#!/usr/bin/env python3

"""
Enhanced Zoom Agent - Combines Selenium automation with Supabase scheduling
Automatically joins Zoom meetings based on database schedule
"""

import os
import sys
import logging
import time
import re
import json
import threading
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, List
from dataclasses import dataclass

# Selenium imports
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.common.keys import Keys

# Database imports
try:
    from supabase import create_client, Client
except ImportError:
    print("Please install supabase: pip install supabase")
    sys.exit(1)

# Environment loading
try:
    from dotenv import load_dotenv
    load_dotenv()  # Load environment variables from .env file
    print("✅ Environment variables loaded from .env file")
except ImportError:
    print("⚠️ python-dotenv not installed. Install with: pip install python-dotenv")
    print("📝 Trying to use system environment variables...")

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler('zoom_agent.log')
    ]
)
logger = logging.getLogger(__name__)

@dataclass
class ScheduledSession:
    course_id: str
    teacher_id: str
    course_title: str
    agent_name: str
    start_time: datetime
    zoom_link: str
    session_type: str
    meeting_id: Optional[str] = None
    password: Optional[str] = None

class EnhancedZoomAgent:
    def __init__(self):
        """Initialize the Zoom Agent with Supabase and Selenium"""
        logger.info("🔧 Initializing Enhanced Zoom Agent...")
        
        # Environment variables
        self.supabase_url = os.getenv("SUPABASE_URL")
        self.supabase_key = os.getenv("SUPABASE_KEY")
        
        if not self.supabase_url or not self.supabase_key:
            raise ValueError("SUPABASE_URL and SUPABASE_KEY environment variables must be set")
        
        # Initialize Supabase client
        self.supabase: Client = create_client(self.supabase_url, self.supabase_key)
        logger.info("✅ Supabase client initialized")
        
        # Agent settings
        self.is_active = False
        self.current_session = None
        self.driver = None
        self.wait = None
        
        # Configuration
        self.join_minutes_early = int(os.getenv("JOIN_MINUTES_EARLY", "1"))  # Join 1 minute early
        self.check_interval_seconds = int(os.getenv("CHECK_INTERVAL_SECONDS", "30"))  # Check every 30 seconds
        self.session_duration_minutes = int(os.getenv("SESSION_DURATION_MINUTES", "60"))  # Stay for 60 minutes
        
        # Track processed sessions to avoid duplicates
        self.processed_sessions = set()
        self.last_join_attempts = {}
        
        logger.info("✅ Enhanced Zoom Agent initialized")

    def setup_chrome_driver(self):
        """Set up Chrome driver with optimal settings"""
        if self.driver:
            return True
            
        try:
            logger.info("🌐 Setting up Chrome driver...")
            
            chrome_options = Options()
            
            # Essential Chrome options
            chrome_options.add_argument("--no-sandbox")
            chrome_options.add_argument("--disable-dev-shm-usage")
            chrome_options.add_argument("--disable-gpu")
            chrome_options.add_argument("--disable-extensions")
            chrome_options.add_argument("--disable-web-security")
            chrome_options.add_argument("--allow-running-insecure-content")
            
            # Media permissions for microphone and camera
            chrome_options.add_argument("--use-fake-ui-for-media-stream")
            chrome_options.add_argument("--use-fake-device-for-media-stream")
            chrome_options.add_argument("--autoplay-policy=no-user-gesture-required")
            
            # Chrome preferences
            prefs = {
                "profile.default_content_setting_values.media_stream_mic": 1,  # Allow microphone
                "profile.default_content_setting_values.media_stream_camera": 2,  # Block camera by default
                "profile.default_content_setting_values.notifications": 1,
                "profile.default_content_setting_values.media_stream": 1,
                "profile.content_settings.exceptions.media_stream_mic": {
                    "https://zoom.us,*": {"setting": 1},
                    "https://us04web.zoom.us,*": {"setting": 1},
                    "https://us05web.zoom.us,*": {"setting": 1}
                }
            }
            chrome_options.add_experimental_option("prefs", prefs)
            
            # Try to create driver
            try:
                # Try system Chrome first
                self.driver = webdriver.Chrome(options=chrome_options)
                logger.info("✅ Using system Chrome")
            except:
                try:
                    # Fallback to webdriver-manager
                    from webdriver_manager.chrome import ChromeDriverManager
                    service = Service(ChromeDriverManager().install())
                    self.driver = webdriver.Chrome(service=service, options=chrome_options)
                    logger.info("✅ Using webdriver-manager Chrome")
                except Exception as e:
                    logger.error(f"❌ Chrome setup failed: {e}")
                    return False
            
            self.driver.maximize_window()
            self.wait = WebDriverWait(self.driver, 10)
            logger.info("✅ Chrome driver ready")
            return True
            
        except Exception as e:
            logger.error(f"❌ Failed to setup Chrome: {e}")
            return False

    def extract_meeting_info(self, url):
        """Extract meeting ID and password from Zoom URL"""
        try:
            meeting_id = re.search(r'/j/(\d+)', url)
            password = re.search(r'pwd=([^&\s]+)', url)
            
            meeting_id = meeting_id.group(1) if meeting_id else None
            password = password.group(1) if password else None
            
            logger.info(f"📋 Meeting ID: {meeting_id}, Password: {'***' if password else 'None'}")
            return meeting_id, password
        except Exception as e:
            logger.error(f"❌ Failed to extract meeting info: {e}")
            return None, None

    def get_today_date_string(self) -> str:
        """Get today's date as ISO string"""
        try:
            today = datetime.now(timezone.utc).date()
            return today.isoformat()
        except Exception as e:
            logger.error(f"❌ Error getting today's date: {e}")
            raise

    def create_start_datetime(self, date_str: str, time_str: str) -> Optional[datetime]:
        """Create datetime object from date and time strings"""
        try:
            time_parts = time_str.split(':')
            hour = int(time_parts[0])
            minute = int(time_parts[1])
            second = int(time_parts[2]) if len(time_parts) > 2 else 0
            
            target_date = datetime.fromisoformat(date_str).date()
            from datetime import time as time_class
            combined_dt = datetime.combine(target_date, time_class(hour, minute, second))
            return combined_dt.replace(tzinfo=timezone.utc)
            
        except Exception as e:
            logger.error(f"❌ Error creating datetime from {date_str} and {time_str}: {e}")
            return None

    def get_agent_name_for_teacher(self, teacher_id: str) -> Optional[str]:
        """Get agent name from database"""
        try:
            response = self.supabase.table('agent_instances').select(
                'agent_name'
            ).eq('current_teacher_id', teacher_id).execute()
            
            if response.data and len(response.data) > 0:
                return response.data[0]['agent_name']
            return "AI Teaching Assistant"  # Default name
            
        except Exception as e:
            logger.error(f"❌ Error getting agent name for teacher {teacher_id}: {e}")
            return "AI Teaching Assistant"

    def get_scheduled_sessions_for_today(self) -> List[ScheduledSession]:
        """Fetch today's scheduled sessions from Supabase"""
        sessions = []
        
        try:
            target_date = self.get_today_date_string()
            logger.info(f"🔍 Looking for sessions on: {target_date}")
            
            # Query courses for today
            response = self.supabase.table('courses').select(
                'id, title, teacher_id, start_date, nextsession, start_time, zoomLink'
            ).or_(
                f'start_date.eq.{target_date},nextsession.eq.{target_date}'
            ).execute()
            
            logger.info(f"📊 Found {len(response.data) if response.data else 0} courses")
            
            if not response.data:
                return sessions
            
            for course in response.data:
                try:
                    session_type = 'new_course' if course.get('start_date') == target_date else 'continuing'
                    
                    zoom_link = course.get('zoomLink', '').strip()
                    if not zoom_link:
                        logger.warning(f"⚠️ No Zoom link for course {course.get('title')}")
                        continue
                    
                    meeting_id, password = self.extract_meeting_info(zoom_link)
                    if not meeting_id:
                        logger.warning(f"⚠️ Could not extract meeting ID from {zoom_link}")
                        continue
                    
                    agent_name = self.get_agent_name_for_teacher(course['teacher_id'])
                    
                    start_time_str = course.get('start_time', '')
                    if not start_time_str:
                        logger.warning(f"⚠️ No start_time for course {course.get('title')}")
                        continue
                    
                    start_time_dt = self.create_start_datetime(target_date, start_time_str)
                    if not start_time_dt:
                        continue
                    
                    session = ScheduledSession(
                        course_id=course['id'],
                        teacher_id=course['teacher_id'],
                        course_title=course['title'],
                        agent_name=agent_name,
                        start_time=start_time_dt,
                        zoom_link=zoom_link,
                        session_type=session_type,
                        meeting_id=meeting_id,
                        password=password
                    )
                    
                    sessions.append(session)
                    logger.info(f"✅ Added session: {session.course_title} at {session.start_time}")
                    
                except Exception as e:
                    logger.error(f"❌ Error processing course {course.get('id', 'Unknown')}: {e}")
                    continue
            
            logger.info(f"📊 Total sessions for today: {len(sessions)}")
            return sessions
            
        except Exception as e:
            logger.error(f"❌ Failed to get scheduled sessions: {e}")
            return sessions

    def should_join_now(self, session: ScheduledSession) -> bool:
        """Check if it's time to join the session"""
        current_time = datetime.now(timezone.utc)
        join_time = session.start_time - timedelta(minutes=self.join_minutes_early)
        time_until_join = (join_time - current_time).total_seconds()
        
        # Join if we're within the join window (1 minute before to 5 minutes after start)
        should_join = -300 <= time_until_join <= 30
        
        if should_join or time_until_join <= 60:  # Log details when close to join time
            logger.info(f"⏰ Join check for {session.course_title}:")
            logger.info(f"  Current time: {current_time}")
            logger.info(f"  Session start: {session.start_time}")
            logger.info(f"  Join time: {join_time}")
            logger.info(f"  Time until join: {time_until_join:.1f} seconds")
            logger.info(f"  Should join: {should_join}")
        
        return should_join

    def join_meeting_selenium(self, session: ScheduledSession) -> bool:
        """Join meeting using Selenium automation"""
        logger.info(f"🚀 JOINING MEETING: {session.course_title}")
        logger.info(f"👤 Agent: {session.agent_name}")
        logger.info(f"🔢 Meeting ID: {session.meeting_id}")
        
        if not self.setup_chrome_driver():
            return False
        
        try:
            meeting_id, url_password = self.extract_meeting_info(session.zoom_link)
            final_password = session.password or url_password
            
            # Strategy 1: Try direct web client URL
            if meeting_id:
                web_url = f"https://zoom.us/wc/join/{meeting_id}"
                if final_password:
                    web_url += f"?pwd={final_password}"
                
                logger.info(f"🌐 Trying web client: {web_url}")
                self.driver.get(web_url)
                time.sleep(5)
                
                if self._complete_join(session.agent_name, final_password):
                    return True
            
            # Strategy 2: Try original URL
            logger.info("🔄 Trying original URL...")
            self.driver.get(session.zoom_link)
            time.sleep(5)
            
            # Look for browser join link
            self._click_browser_join()
            time.sleep(3)
            
            return self._complete_join(session.agent_name, final_password)
            
        except Exception as e:
            logger.error(f"❌ Join failed: {e}")
            return False

    def _click_browser_join(self):
        """Find and click browser join link"""
        logger.info("🔍 Looking for browser join...")
        
        selectors = [
            "//a[contains(text(), 'Join from your browser')]",
            "//a[contains(text(), 'click here')]",
            "//a[contains(@href, 'wc/join')]"
        ]
        
        for selector in selectors:
            try:
                element = self.wait.until(EC.element_to_be_clickable((By.XPATH, selector)))
                element.click()
                logger.info("✅ Clicked browser join")
                return True
            except:
                continue
        
        logger.info("⚠️ No browser join found")
        return False

    def _complete_join(self, name, password):
        """Complete the join process with enhanced reliability"""
        logger.info("🔍 Completing join...")
        
        # Wait for join form
        try:
            WebDriverWait(self.driver, 15).until(
                EC.presence_of_element_located((By.XPATH, "//input[contains(@placeholder, 'Name') or @id='inputname']"))
            )
            logger.info("✅ Join form loaded")
        except:
            logger.warning("⚠️ Join form may not be fully loaded")
        
        time.sleep(3)  # Additional stability wait
        
        # Enter name
        name_entered = self._enter_name(name)
        
        # Enter password if needed
        password_entered = True
        if password:
            password_entered = self._enter_password(password)
        
        # Click join button
        join_clicked = self._click_join_button()
        
        if join_clicked:
            logger.info("🎯 Join button clicked successfully!")
            time.sleep(15)  # Wait for meeting to load
            
            # Handle audio and video
            self._handle_media_setup()
            time.sleep(5)
            
            # Verify join
            return self._verify_joined()
        else:
            logger.error("❌ Could not click join button")
            return False

    def _enter_name(self, name):
        """Enter name in the join form"""
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
                name_field.clear()
                time.sleep(0.5)
                name_field.send_keys(name)
                logger.info(f"✅ Entered name: {name}")
                return True
            except:
                continue
        
        logger.warning("⚠️ Could not find name field")
        return False

    def _enter_password(self, password):
        """Enter password in the join form"""
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
                logger.info("✅ Entered password")
                return True
            except:
                continue
        
        logger.warning("⚠️ Could not find password field")
        return False

    def _click_join_button(self):
        """Click the join button with multiple fallback strategies"""
        join_selectors = [
            "//button[contains(text(), 'Join')]",
            "//button[@id='btnSubmit']",
            "//a[@id='btnSubmit']", 
            "//input[@type='submit']",
            "//button[@type='submit']",
            "//input[@value='Join']",
            "//button[contains(@class, 'btn') and contains(text(), 'Join')]"
        ]
        
        time.sleep(2)  # Wait for form stability
        
        # Try standard selectors first
        for selector in join_selectors:
            try:
                join_btn = WebDriverWait(self.driver, 5).until(
                    EC.element_to_be_clickable((By.XPATH, selector))
                )
                self.driver.execute_script("arguments[0].scrollIntoView(true);", join_btn)
                time.sleep(1)
                join_btn.click()
                logger.info("✅ Clicked join button")
                return True
            except:
                continue
        
        # Fallback strategies
        logger.info("⚠️ Standard join button not found, trying fallbacks...")
        
        # JavaScript fallback
        try:
            self.driver.execute_script("""
                var buttons = document.querySelectorAll('button, input[type="submit"], a');
                for(var i = 0; i < buttons.length; i++) {
                    var text = buttons[i].textContent || buttons[i].value || '';
                    if(text.toLowerCase().includes('join')) {
                        buttons[i].click();
                        return true;
                    }
                }
                return false;
            """)
            logger.info("✅ Used JavaScript join fallback")
            return True
        except:
            pass
        
        # Enter key fallback
        try:
            self.driver.find_element(By.TAG_NAME, "body").send_keys(Keys.RETURN)
            logger.info("✅ Used Enter key fallback")
            return True
        except:
            pass
        
        return False

    def _handle_media_setup(self):
        """Handle audio and video setup - Enable mic, disable camera"""
        logger.info("🎤 Setting up media (mic ON, camera OFF)...")
        
        time.sleep(8)  # Wait for media prompts
        
        # Join with computer audio
        audio_selectors = [
            "//button[contains(text(), 'Join with Computer Audio')]",
            "//button[contains(text(), 'Join Audio')]", 
            "//button[contains(text(), 'Computer Audio')]"
        ]
        
        for selector in audio_selectors:
            try:
                audio_btn = WebDriverWait(self.driver, 5).until(
                    EC.element_to_be_clickable((By.XPATH, selector))
                )
                audio_btn.click()
                logger.info("✅ Joined with computer audio")
                time.sleep(3)
                break
            except:
                continue
        
        # Ensure microphone is unmuted
        self._unmute_microphone()
        
        # Ensure camera is off
        self._ensure_camera_off()
        
        # Handle media permissions
        self._handle_media_permissions()

    def _unmute_microphone(self):
        """Unmute the microphone"""
        logger.info("🎤 Enabling microphone...")
        time.sleep(3)
        
        mic_selectors = [
            "//button[contains(@aria-label, 'unmute') or contains(@title, 'unmute')]",
            "//button[contains(@aria-label, 'Unmute') or contains(@title, 'Unmute')]",
            "//button[contains(@class, 'muted')]"
        ]
        
        for selector in mic_selectors:
            try:
                mic_btn = self.driver.find_element(By.XPATH, selector)
                mic_btn.click()
                logger.info("✅ Microphone enabled")
                return True
            except:
                continue
        
        logger.info("ℹ️ Microphone status unclear or already enabled")
        return False

    def _ensure_camera_off(self):
        """Ensure camera stays OFF"""
        logger.info("📹❌ Ensuring camera stays OFF...")
        time.sleep(3)
        
        camera_off_selectors = [
            "//button[contains(@aria-label, 'Stop Video') or contains(@title, 'Stop Video')]",
            "//button[contains(@aria-label, 'Turn off camera') or contains(@title, 'Turn off camera')]"
        ]
        
        for selector in camera_off_selectors:
            try:
                camera_btn = self.driver.find_element(By.XPATH, selector)
                aria_label = camera_btn.get_attribute('aria-label') or ''
                
                if 'stop' in aria_label.lower() or 'turn off' in aria_label.lower():
                    camera_btn.click()
                    logger.info("✅ Camera turned OFF")
                    return True
            except:
                continue
        
        logger.info("✅ Camera already OFF or not found")
        return False

    def _handle_media_permissions(self):
        """Handle browser media permission dialogs"""
        logger.info("🔧 Handling media permissions...")
        time.sleep(5)
        
        # Try to click Allow buttons
        allow_selectors = [
            "//button[contains(text(), 'Allow')]",
            "//button[@aria-label='Allow']"
        ]
        
        for selector in allow_selectors:
            try:
                allow_btn = WebDriverWait(self.driver, 2).until(
                    EC.element_to_be_clickable((By.XPATH, selector))
                )
                allow_btn.click()
                logger.info("✅ Allowed media permissions")
                return True
            except:
                continue
        
        logger.info("ℹ️ Media permissions handled or not required")

    def _verify_joined(self):
        """Verify that we successfully joined the meeting"""
        indicators = [
            "//button[contains(@title, 'mute')]",
            "//button[contains(@title, 'Mute')]",
            "//button[contains(text(), 'Participants')]",
            "//button[contains(text(), 'Chat')]"
        ]
        
        for indicator in indicators:
            try:
                self.driver.find_element(By.XPATH, indicator)
                logger.info("🎉 Successfully joined meeting!")
                return True
            except:
                continue
        
        # Check URL as fallback
        if any(x in self.driver.current_url for x in ['/wc/', '/j/']):
            logger.info("🎉 Joined (URL check)")
            return True
        
        logger.warning("⚠️ Join status unclear")
        return False

    def send_message(self, message):
        """Send a chat message in the meeting"""
        logger.info(f"💬 Sending: {message}")
        
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
            logger.info("✅ Message sent")
            
        except Exception as e:
            logger.warning(f"⚠️ Could not send message: {e}")

    def manage_session(self, session: ScheduledSession):
        """Manage the session after joining"""
        logger.info(f"📚 Managing session: {session.course_title}")
        
        # Send welcome message
        welcome_msg = f"🤖 {session.agent_name} has joined the session! Ready to assist with learning. 📚🎤"
        self.send_message(welcome_msg)
        
        # Stay active for the session duration
        end_time = time.time() + (self.session_duration_minutes * 60)
        message_count = 1
        
        while time.time() < end_time and self.is_active:
            try:
                # Send periodic status messages
                if message_count % 10 == 0:  # Every 10 minutes
                    status_msg = f"📚 {session.agent_name} is actively monitoring the session and ready to help! ({message_count//10 * 10} min)"
                    self.send_message(status_msg)
                
                time.sleep(60)  # Wait 1 minute
                message_count += 1
                
            except KeyboardInterrupt:
                logger.info("🛑 Session stopped by user")
                break
            except Exception as e:
                logger.error(f"❌ Error during session management: {e}")
                break
        
        logger.info("✅ Session management completed")

    def leave_meeting(self):
        """Leave the meeting and cleanup"""
        logger.info("👋 Leaving meeting...")
        
        if not self.driver:
            return
        
        try:
            # Try to leave gracefully
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
            logger.warning(f"⚠️ Could not leave gracefully: {e}")
        
        finally:
            try:
                self.driver.quit()
                self.driver = None
                self.wait = None
                logger.info("✅ Browser closed")
            except:
                pass

    def run_agent(self):
        """Main agent loop"""
        logger.info("🤖 Starting Enhanced Zoom Agent...")
        logger.info(f"⚙️ Check interval: {self.check_interval_seconds} seconds")
        logger.info(f"⚙️ Join timing: {self.join_minutes_early} minutes early")
        logger.info(f"⚙️ Session duration: {self.session_duration_minutes} minutes")
        
        self.is_active = True
        
        try:
            while self.is_active:
                current_time = datetime.now(timezone.utc)
                logger.info(f"🔄 Checking for sessions at {current_time.strftime('%H:%M:%S')}")
                
                sessions = self.get_scheduled_sessions_for_today()
                
                if not sessions:
                    logger.info("📅 No sessions found for today")
                else:
                    logger.info(f"📊 Found {len(sessions)} sessions for today")
                
                for session in sessions:
                    session_key = f"{session.course_id}_{session.start_time.isoformat()}"
                    
                    # Skip if already processed
                    if session_key in self.processed_sessions:
                        continue
                    
                    if self.should_join_now(session):
                        # Check if we've tried recently (prevent spam)
                        last_attempt = self.last_join_attempts.get(session_key, 0)
                        current_timestamp = time.time()
                        
                        if current_timestamp - last_attempt < 120:  # Wait 2 minutes between attempts
                            logger.info("⏳ Join attempted recently, skipping...")
                            continue
                        
                        logger.info(f"🚀 TIME TO JOIN: {session.course_title}")
                        
                        # Record attempt
                        self.last_join_attempts[session_key] = current_timestamp
                        
                        # Attempt to join
                        success = self.join_meeting_selenium(session)
                        
                        if success:
                            logger.info("🎉 Successfully joined meeting!")
                            self.current_session = session
                            
                            # Manage the session in a separate thread
                            session_thread = threading.Thread(
                                target=self.manage_session, 
                                args=(session,),
                                daemon=True
                            )
                            session_thread.start()
                            
                            # Mark as processed
                            self.processed_sessions.add(session_key)
                            
                            # Wait for session to complete
                            session_thread.join(timeout=self.session_duration_minutes * 60 + 300)  # 5 min buffer
                            
                            # Leave meeting
                            self.leave_meeting()
                            self.current_session = None
                            
                        else:
                            logger.error("❌ Failed to join meeting")
                    
                    else:
                        time_until = (session.start_time - current_time).total_seconds()
                        if time_until > 0:
                            logger.info(f"⏰ {session.course_title} starts in {time_until/60:.1f} minutes")
                        elif time_until < -1800:  # 30 minutes past start time
                            # Mark very old sessions as processed to avoid clutter
                            logger.info(f"🕐 Session {session.course_title} is too old, marking as processed")
                            self.processed_sessions.add(session_key)
                
                # Sleep before next check
                time.sleep(self.check_interval_seconds)
        
        except KeyboardInterrupt:
            logger.info("🛑 Agent stopped by user")
        except Exception as e:
            logger.error(f"❌ Agent error: {e}")
            import traceback
            logger.error(f"Full traceback: {traceback.format_exc()}")
        finally:
            # Cleanup
            if self.current_session:
                self.leave_meeting()
            self.is_active = False
            logger.info("✅ Agent shutdown complete")


def main():
    """Main function to run the agent"""
    print("🤖 Enhanced Zoom Agent with Supabase Integration")
    print("=" * 60)
    print("✅ Automatically joins scheduled Zoom meetings")
    print("✅ Fetches schedule from Supabase database")
    print("✅ Joins 1 minute before session start time")
    print("✅ Manages audio/video (mic ON, camera OFF)")
    print("✅ Sends greeting and status messages")
    print("✅ Stays active for full session duration")
    print("=" * 60)
    
    # Check environment variables
    required_env_vars = ["SUPABASE_URL", "SUPABASE_KEY"]
    missing_vars = [var for var in required_env_vars if not os.getenv(var)]
    
    if missing_vars:
        print(f"❌ Missing required environment variables: {', '.join(missing_vars)}")
        print("\nPlease set them before running:")
        for var in missing_vars:
            print(f"export {var}='your_value_here'")
        return
    
    print("✅ Environment variables configured")
    print(f"📊 Supabase URL: {os.getenv('SUPABASE_URL')[:30]}...")
    print()
    
    # Configuration summary
    join_early = int(os.getenv("JOIN_MINUTES_EARLY", "1"))
    check_interval = int(os.getenv("CHECK_INTERVAL_SECONDS", "30"))
    session_duration = int(os.getenv("SESSION_DURATION_MINUTES", "60"))
    
    print("⚙️ Configuration:")
    print(f"   Join timing: {join_early} minute(s) before session start")
    print(f"   Check interval: {check_interval} seconds")
    print(f"   Session duration: {session_duration} minutes")
    print()
    
    try:
        # Create and run agent
        agent = EnhancedZoomAgent()
        
        print("🚀 Starting agent...")
        print("📋 The agent will:")
        print("   1. Check database every 30 seconds for today's sessions")
        print("   2. Join meetings 1 minute before start time")
        print("   3. Enable microphone and disable camera")
        print("   4. Send greeting message to participants")
        print("   5. Stay active for the full session duration")
        print("   6. Leave meeting automatically when done")
        print()
        print("🛑 Press Ctrl+C to stop the agent")
        print("=" * 60)
        
        agent.run_agent()
        
    except KeyboardInterrupt:
        print("\n👋 Agent stopped by user. Goodbye!")
    except Exception as e:
        logger.error(f"❌ Failed to start agent: {e}")
        print(f"❌ Error: {e}")
        print("\n🔧 Troubleshooting tips:")
        print("   1. Check your environment variables")
        print("   2. Ensure Chrome/Chromium is installed")
        print("   3. Check your internet connection")
        print("   4. Verify Supabase credentials and permissions")


if __name__ == "__main__":
    main()