#!/usr/bin/env python3

"""
Integrated AI Teacher Zoom Agent
Automatically joins scheduled courses and plays generated lesson audio
"""

import os
import time
import json
import logging
import asyncio
import threading
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, List
from dataclasses import dataclass
import requests
import pygame
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.common.keys import Keys

try:
    from src.integrations.supabase_client import SupabaseClient
    from supabase import create_client
except Exception:
    from supabase_client import SupabaseClient
    from supabase import create_client

logger = logging.getLogger(__name__)

@dataclass
class ScheduledCourse:
    course_id: str
    teacher_id: str
    course_title: str
    start_time: datetime
    zoom_link: str
    lesson_audio_url: Optional[str] = None
    lesson_id: Optional[str] = None

class AudioPlayer:
    """Handle audio playback during Zoom sessions"""
    
    def __init__(self):
        pygame.mixer.init()
        self.current_audio = None
        self.is_playing = False
        
    def download_audio(self, audio_url: str, temp_path: str) -> bool:
        """Download audio file from URL"""
        try:
            response = requests.get(audio_url, stream=True, timeout=30)
            response.raise_for_status()
            
            with open(temp_path, 'wb') as f:
                for chunk in response.iter_content(chunk_size=8192):
                    f.write(chunk)
            
            return True
        except Exception as e:
            logger.error(f"Failed to download audio: {e}")
            return False
    
    def play_audio(self, audio_path: str) -> bool:
        """Play audio file"""
        try:
            pygame.mixer.music.load(audio_path)
            pygame.mixer.music.play()
            self.is_playing = True
            logger.info(f"Started playing audio: {audio_path}")
            return True
        except Exception as e:
            logger.error(f"Failed to play audio: {e}")
            return False
    
    def stop_audio(self):
        """Stop audio playback"""
        try:
            pygame.mixer.music.stop()
            self.is_playing = False
            logger.info("Stopped audio playback")
        except Exception as e:
            logger.error(f"Failed to stop audio: {e}")
    
    def is_audio_playing(self) -> bool:
        """Check if audio is currently playing"""
        return pygame.mixer.music.get_busy()

class IntegratedZoomTeacher:
    """AI Teacher that automatically joins Zoom meetings and plays lesson audio"""
    
    def __init__(self):
        self.driver = None
        self.audio_player = AudioPlayer()
        self.current_session = None
        self.is_active = False
        
        # Supabase setup
        self.supabase_url = os.getenv("SUPABASE_URL")
        self.supabase_key = os.getenv("SUPABASE_KEY")
        self.supabase = create_client(self.supabase_url, self.supabase_key)
        
    def setup_chrome(self):
        """Initialize Chrome with optimized settings for Zoom"""
        logger.info("Setting up Chrome for Zoom sessions...")
        
        chrome_options = Options()
        
        # Essential Chrome options
        chrome_options.add_argument("--no-sandbox")
        chrome_options.add_argument("--disable-dev-shm-usage")
        chrome_options.add_argument("--disable-gpu")
        chrome_options.add_argument("--disable-extensions")
        
        # Audio/Media permissions
        chrome_options.add_argument("--use-fake-ui-for-media-stream")
        chrome_options.add_argument("--use-fake-device-for-media-stream")
        chrome_options.add_argument("--allow-running-insecure-content")
        chrome_options.add_argument("--disable-web-security")
        chrome_options.add_argument("--autoplay-policy=no-user-gesture-required")
        
        # Chrome preferences for media permissions
        prefs = {
            "profile.default_content_setting_values.media_stream_mic": 1,
            "profile.default_content_setting_values.media_stream_camera": 2,
            "profile.default_content_setting_values.notifications": 1,
            "profile.default_content_setting_values.media_stream": 1,
            "profile.content_settings.exceptions.media_stream_mic": {
                "https://zoom.us,*": {"setting": 1},
                "https://us04web.zoom.us,*": {"setting": 1}
            },
        }
        chrome_options.add_experimental_option("prefs", prefs)
        
        try:
            self.driver = webdriver.Chrome(options=chrome_options)
            self.driver.maximize_window()
            self.wait = WebDriverWait(self.driver, 10)
            logger.info("Chrome setup successful")
            return True
        except Exception as e:
            logger.error(f"Chrome setup failed: {e}")
            return False
    
    def get_scheduled_courses(self, teacher_id: Optional[str] = None) -> List[ScheduledCourse]:
        """Get courses scheduled for today"""
        try:
            current_utc = datetime.now(timezone.utc)
            today = current_utc.date()
            
            # Build query - since start_time is TIME, not TIMESTAMPTZ, we don't filter by date
            query = self.supabase.table('courses').select(
                'id, title, teacher_id, start_time, zoomLink'
            )
            
            if teacher_id:
                query = query.eq('teacher_id', teacher_id)
            
            # Get all courses for this teacher (can't filter by date with TIME column)
            response = query.execute()
            
            scheduled_courses = []
            
            for course in response.data:
                try:
                    # Since start_time is TIME format (HH:MM:SS), create a datetime for today
                    start_time_str = course['start_time']  # This will be like "14:30:00"
                    
                    # Parse the time and create a datetime for today
                    from datetime import datetime, time
                    time_parts = start_time_str.split(':')
                    hour = int(time_parts[0])
                    minute = int(time_parts[1])
                    second = int(time_parts[2]) if len(time_parts) > 2 else 0
                    
                    # Create datetime for today with the course time
                    start_time = datetime.combine(
                        today, 
                        time(hour, minute, second)
                    ).replace(tzinfo=timezone.utc)
                    
                    scheduled_course = ScheduledCourse(
                        course_id=course['id'],
                        teacher_id=course['teacher_id'],
                        course_title=course['title'],
                        start_time=start_time,
                        zoom_link=course.get('zoomLink', '')
                    )
                    
                    # Get lesson audio if available
                    audio_url = self.get_lesson_audio_url(course['id'], course['teacher_id'], start_time.date())
                    if audio_url:
                        scheduled_course.lesson_audio_url = audio_url
                        # Also get the lesson ID for reference
                        lesson_response = self.supabase.table('prepared_lessons').select(
                            'lesson_id'
                        ).eq('teacher_id', course['teacher_id']).eq('audio_url', audio_url).execute()
                        if lesson_response.data:
                            scheduled_course.lesson_id = lesson_response.data[0]['lesson_id']
                    
                    scheduled_courses.append(scheduled_course)
                    
                except Exception as e:
                    logger.error(f"Error processing course {course['id']}: {e}")
                    continue
            
            # Filter to only courses for today after creating the datetime objects
            today_courses = [
                course for course in scheduled_courses 
                if course.start_time.date() == today
            ]
            
            logger.info(f"Found {len(today_courses)} scheduled courses for today")
            return today_courses
            
        except Exception as e:
            logger.error(f"Failed to get scheduled courses: {e}")
            return []
    
    def get_lesson_audio_url(self, course_id: str, teacher_id: str, date) -> Optional[str]:
        """Get the audio URL for lessons in a specific course for a specific teacher and date"""
        try:
            # Get prepared lessons with audio for this course and teacher
            response = self.supabase.table('prepared_lessons').select(
                'lesson_id, url, audio_url'
            ).eq('teacher_id', teacher_id).not_.is_('audio_url', 'null').execute()
            
            if response.data:
                # Find lessons that match this course by checking the lesson belongs to the course
                for lesson in response.data:
                    # Get the lesson details to check if it belongs to this course
                    lesson_response = self.supabase.table('lessons').select(
                        'id, course_id'
                    ).eq('id', lesson['lesson_id']).eq('course_id', course_id).execute()
                    
                    if lesson_response.data:
                        # Found a lesson with audio that belongs to this course
                        return lesson['audio_url']
            
            return None
            
        except Exception as e:
            logger.error(f"Failed to get lesson audio URL: {e}")
            return None
    
    def join_zoom_meeting(self, zoom_url: str, teacher_name: str = "AI Teacher") -> bool:
        """Join Zoom meeting using the existing logic"""
        logger.info(f"Joining Zoom meeting: {zoom_url}")
        
        try:
            # Use the existing Zoom joining logic
            self.driver.get(zoom_url)
            time.sleep(5)
            
            # Click browser join link
            self._click_browser_join()
            time.sleep(3)
            
            # Complete join process
            return self._complete_join(teacher_name)
            
        except Exception as e:
            logger.error(f"Failed to join Zoom meeting: {e}")
            return False
    
    def _click_browser_join(self):
        """Find and click browser join link"""
        selectors = [
            "//a[contains(text(), 'Join from your browser')]",
            "//a[contains(text(), 'click here')]",
            "//a[contains(@href, 'wc/join')]"
        ]
        
        for selector in selectors:
            try:
                element = self.wait.until(EC.element_to_be_clickable((By.XPATH, selector)))
                element.click()
                logger.info("Clicked browser join")
                return True
            except:
                continue
        
        logger.warning("No browser join found")
        return False
    
    def _complete_join(self, name: str) -> bool:
        """Complete the join process"""
        try:
            # Wait for join form
            WebDriverWait(self.driver, 10).until(
                EC.presence_of_element_located((By.XPATH, "//input[contains(@placeholder, 'Name') or @id='inputname']"))
            )
            time.sleep(2)
            
            # Enter name
            name_selectors = [
                "//input[@id='inputname']",
                "//input[@placeholder='Your Name']",
                "//input[contains(@class, 'form-control') and @type='text']"
            ]
            
            for selector in name_selectors:
                try:
                    name_field = WebDriverWait(self.driver, 3).until(
                        EC.presence_of_element_located((By.XPATH, selector))
                    )
                    name_field.clear()
                    name_field.send_keys(name)
                    break
                except:
                    continue
            
            # Click join button
            join_selectors = [
                "//button[contains(text(), 'Join')]",
                "//button[@id='btnSubmit']",
                "//input[@type='submit']"
            ]
            
            for selector in join_selectors:
                try:
                    join_btn = WebDriverWait(self.driver, 5).until(
                        EC.element_to_be_clickable((By.XPATH, selector))
                    )
                    join_btn.click()
                    break
                except:
                    continue
            
            # Wait for meeting interface
            time.sleep(15)
            
            # Handle audio setup
            self._setup_audio()
            
            return self._verify_joined()
            
        except Exception as e:
            logger.error(f"Failed to complete join: {e}")
            return False
    
    def _setup_audio(self):
        """Setup audio - enable microphone, disable camera"""
        logger.info("Setting up audio (mic ON, camera OFF)")
        
        # Join with computer audio
        audio_selectors = [
            "//button[contains(text(), 'Join with Computer Audio')]",
            "//button[contains(text(), 'Join Audio')]"
        ]
        
        for selector in audio_selectors:
            try:
                audio_btn = WebDriverWait(self.driver, 5).until(
                    EC.element_to_be_clickable((By.XPATH, selector))
                )
                audio_btn.click()
                logger.info("Joined with computer audio")
                break
            except:
                continue
        
        # Ensure microphone is unmuted
        time.sleep(3)
        try:
            # Look for muted mic button
            mic_btn = self.driver.find_element(By.XPATH, 
                "//button[contains(@aria-label, 'unmute') or contains(@class, 'muted')]")
            mic_btn.click()
            logger.info("Unmuted microphone")
        except:
            logger.info("Microphone already enabled")
        
        # Ensure camera is off
        try:
            camera_btn = self.driver.find_element(By.XPATH,
                "//button[contains(@aria-label, 'Stop Video')]")
            camera_btn.click()
            logger.info("Camera turned off")
        except:
            logger.info("Camera already off")
    
    def _verify_joined(self) -> bool:
        """Verify successful meeting join"""
        indicators = [
            "//button[contains(@title, 'mute')]",
            "//button[contains(text(), 'Participants')]",
            "//button[contains(text(), 'Chat')]"
        ]
        
        for indicator in indicators:
            try:
                self.driver.find_element(By.XPATH, indicator)
                logger.info("Successfully joined meeting")
                return True
            except:
                continue
        
        return False
    
    def send_chat_message(self, message: str):
        """Send a chat message in the meeting"""
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
            logger.info(f"Sent chat message: {message}")
            
        except Exception as e:
            logger.error(f"Failed to send chat message: {e}")
    
    def play_lesson_audio(self, audio_url: str):
        """Download and play lesson audio"""
        if not audio_url:
            logger.warning("No audio URL provided")
            return
        
        try:
            # Create temp file for audio
            temp_audio_path = f"temp_lesson_{int(time.time())}.mp3"
            
            # Download audio
            logger.info(f"Downloading lesson audio: {audio_url}")
            if self.audio_player.download_audio(audio_url, temp_audio_path):
                logger.info("Audio downloaded successfully")
                
                # Send chat notification
                self.send_chat_message("🎓 Starting lesson audio playback...")
                
                # Play audio
                if self.audio_player.play_audio(temp_audio_path):
                    logger.info("Audio playback started")
                    
                    # Wait for audio to finish
                    while self.audio_player.is_audio_playing():
                        time.sleep(1)
                    
                    logger.info("Audio playback completed")
                    self.send_chat_message("✅ Lesson audio completed!")
                
                # Clean up temp file
                try:
                    os.remove(temp_audio_path)
                except:
                    pass
            
        except Exception as e:
            logger.error(f"Failed to play lesson audio: {e}")
            self.send_chat_message("⚠️ Audio playback failed. Please contact support.")
    
    def conduct_session(self, course: ScheduledCourse):
        """Conduct a complete teaching session"""
        logger.info(f"Starting session for course: {course.course_title}")
        
        try:
            # Join the meeting
            if not self.join_zoom_meeting(course.zoom_link, f"AI Teacher - {course.course_title}"):
                logger.error("Failed to join meeting")
                return False
            
            # Send welcome message with course details
            welcome_msg = f"AI Teacher has joined! Starting lesson for '{course.course_title}'"
            if course.lesson_id:
                welcome_msg += f" (Lesson ID: {course.lesson_id[:8]}...)"
            self.send_chat_message(welcome_msg)
            
            # Wait a moment for participants to see the message
            time.sleep(5)
            
            # Play lesson audio if available
            if course.lesson_audio_url:
                logger.info(f"Found lesson audio for course {course.course_id}")
                self.send_chat_message("Preparing to start lesson audio playback...")
                time.sleep(3)
                self.play_lesson_audio(course.lesson_audio_url)
            else:
                # No audio available - provide alternative content
                logger.warning(f"No lesson audio found for course {course.course_id}")
                self.send_chat_message("Audio content not available for this lesson.")
                self.send_chat_message("Please check your prepared lesson materials or contact support.")
                
                # Stay in meeting for a reasonable time even without audio
                self.send_chat_message("I'll stay in the meeting for 10 minutes in case you need assistance.")
                time.sleep(600)  # Stay for 10 minutes
            
            # End session
            self.send_chat_message("Lesson session completed. Thank you for attending!")
            logger.info(f"Session completed for course: {course.course_title}")
            
            return True
            
        except Exception as e:
            logger.error(f"Session failed: {e}")
            try:
                self.send_chat_message("Technical difficulties encountered. Please contact support.")
            except:
                pass
            return False
    
    def leave_meeting(self):
        """Leave the current meeting"""
        try:
            leave_btn = self.driver.find_element(By.XPATH, 
                "//button[contains(text(), 'Leave')]")
            leave_btn.click()
            time.sleep(2)
            
            # Confirm leave
            try:
                confirm_btn = self.driver.find_element(By.XPATH, 
                    "//button[contains(text(), 'Leave Meeting')]")
                confirm_btn.click()
            except:
                pass
                
            logger.info("Left meeting")
            
        except Exception as e:
            logger.error(f"Failed to leave meeting: {e}")
    
    def schedule_and_run_sessions(self, teacher_id: Optional[str] = None):
        """Main method to schedule and run teaching sessions"""
        logger.info("Starting AI Teacher Zoom Agent...")
        
        if not self.setup_chrome():
            logger.error("Failed to setup Chrome")
            return
        
        self.is_active = True
        
        try:
            while self.is_active:
                # Get scheduled courses
                courses = self.get_scheduled_courses(teacher_id)
                
                current_time = datetime.now(timezone.utc)
                
                for course in courses:
                    # Check if it's time to join
                    time_until_start = (course.start_time - current_time).total_seconds()
                    
                    # Join 2 minutes before scheduled time
                    if -120 <= time_until_start <= 120:
                        logger.info(f"Time to join course: {course.course_title}")
                        
                        if course.zoom_link:
                            success = self.conduct_session(course)
                            
                            if success:
                                logger.info(f"Session completed: {course.course_title}")
                            else:
                                logger.error(f"Session failed: {course.course_title}")
                            
                            # Leave meeting
                            self.leave_meeting()
                            time.sleep(5)
                        else:
                            logger.warning(f"No Zoom link for course: {course.course_title}")
                
                # Wait before checking again
                time.sleep(60)  # Check every minute
        
        except KeyboardInterrupt:
            logger.info("Stopping AI Teacher Agent...")
            self.is_active = False
        
        except Exception as e:
            logger.error(f"Agent error: {e}")
        
        finally:
            self.cleanup()
    
    def cleanup(self):
        """Clean up resources"""
        try:
            if self.audio_player:
                self.audio_player.stop_audio()
            
            if self.driver:
                self.driver.quit()
                
            logger.info("Cleanup completed")
        except Exception as e:
            logger.error(f"Cleanup error: {e}")


def main():
    """Main execution function"""
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    
    print("AI Teacher Zoom Agent")
    print("=" * 40)
    print("Features:")
    print("- Automatic scheduling based on course start_time")
    print("- Plays generated lesson audio")
    print("- Interactive chat messages")
    print("- Audio ON, Camera OFF")
    print()
    
    # Get teacher ID from command line or environment
    import sys
    teacher_id = None
    if len(sys.argv) > 1:
        teacher_id = sys.argv[1]
    else:
        teacher_id = os.getenv("TEACHER_ID")
    
    if teacher_id:
        print(f"Running for teacher: {teacher_id}")
    else:
        print("Running for all teachers")
    
    # Create and run the agent
    agent = IntegratedZoomTeacher()
    
    try:
        # Run for specific teacher or all teachers
        agent.schedule_and_run_sessions(teacher_id=teacher_id)
        
    except Exception as e:
        logger.error(f"Failed to start agent: {e}")
    
    finally:
        agent.cleanup()


if __name__ == "__main__":
    main()