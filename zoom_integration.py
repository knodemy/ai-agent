#!/usr/bin/env python3

"""
Zoom Meeting SDK Agent - Using Official Zoom SDK
This is an alternative approach using Zoom's Meeting SDK instead of browser automation
"""

import os
import sys
import logging
from datetime import datetime as DT, timedelta as TD, timezone as TZ
from typing import Optional, Dict, List
from dataclasses import dataclass
import time as TIME_MODULE
import requests
import jwt
import base64

# Add the project root to Python path
project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
src_path = os.path.join(project_root, 'src')
sys.path.insert(0, src_path)

try:
    from src.integrations.supabase_client import SupabaseClient
    from supabase import create_client
except Exception as e:
    print(f"Could not import required modules: {e}")
    sys.exit(1)

logger = logging.getLogger(__name__)

@dataclass
class ScheduledSession:
    course_id: str
    teacher_id: str
    course_title: str
    agent_name: str
    start_time: DT
    zoom_link: str
    session_type: str
    meeting_id: Optional[str] = None
    password: Optional[str] = None

class ZoomSDKAgent:
    def __init__(self):
        self.is_active = False
        self.current_session = None
        
        # Zoom SDK Credentials
        self.zoom_sdk_key = os.getenv("ZOOM_SDK_KEY", "1YyQtzNSDOv486zqgo2kQ")
        self.zoom_sdk_secret = os.getenv("ZOOM_SDK_SECRET", "P8wDVgRZNxI2VB4ObeV2h4lPC5fUaFQF")
        
        if not self.zoom_sdk_key or not self.zoom_sdk_secret:
            raise ValueError("ZOOM_SDK_KEY and ZOOM_SDK_SECRET must be set")
        
        # Supabase setup
        self.supabase_url = os.getenv("SUPABASE_URL")
        self.supabase_key = os.getenv("SUPABASE_KEY")
        if not self.supabase_url or not self.supabase_key:
            raise ValueError("SUPABASE_URL and SUPABASE_KEY must be set")
        
        self.supabase = create_client(self.supabase_url, self.supabase_key)
        
        # Settings
        self.join_minutes_early = int(os.getenv("JOIN_MINUTES_EARLY", "0"))
        self.check_interval_seconds = int(os.getenv("CHECK_INTERVAL_SECONDS", "10"))

    def generate_sdk_jwt(self, meeting_number: str, role: int = 0) -> str:
        """Generate JWT signature for Zoom Web SDK"""
        try:
            import time
            
            # Current timestamp
            iat = int(time.time())
            exp = iat + 7200  # 2 hours expiration
            
            # For Zoom Web SDK, we need a signature, not a full JWT
            # The signature format is different from regular JWT
            payload = {
                "sdkKey": self.zoom_sdk_key,  # Fixed: use self.zoom_sdk_key
                "mn": str(meeting_number),
                "role": role,
                "iat": iat,
                "exp": exp,
                "tokenExp": exp,
            }
                    
            # Generate the signature
            signature = jwt.encode(payload, self.zoom_sdk_secret, algorithm='HS256')
            
            # Handle different PyJWT versions
            if isinstance(signature, bytes):
                signature = signature.decode('utf-8')
                
            logger.info(f"Generated Web SDK signature")
            logger.info(f"Meeting number: {meeting_number}")
            logger.info(f"SDK Key: {self.zoom_sdk_key}")
            logger.info(f"Signature preview: {signature[:50]}...")
            
            return signature
            
        except Exception as e:
            logger.error(f"Failed to generate signature: {e}")
            import traceback
            logger.error(f"Full traceback: {traceback.format_exc()}")
            return None

    def extract_meeting_info_from_url(self, zoom_url: str) -> tuple:
        """Extract meeting ID and password from Zoom URL"""
        try:
            import re
            
            # Extract meeting ID
            meeting_id_patterns = [
                r'/j/(\d+)',  # Standard format
                r'meetingId=(\d+)',  # Alternative format
                r'meeting_id=(\d+)'  # Another format
            ]
            
            meeting_id = None
            for pattern in meeting_id_patterns:
                match = re.search(pattern, zoom_url)
                if match:
                    meeting_id = match.group(1)
                    break
            
            # Extract password
            password_patterns = [
                r'pwd=([^&\s]+)',
                r'password=([^&\s]+)',
                r'passcode=([^&\s]+)'
            ]
            
            password = None
            for pattern in password_patterns:
                match = re.search(pattern, zoom_url)
                if match:
                    password = match.group(1)
                    break
            
            logger.info(f"📋 Meeting ID: {meeting_id}, Password: {'***' if password else 'None'}")
            return meeting_id, password
            
        except Exception as e:
            logger.error(f"❌ Failed to extract meeting info: {e}")
            return None, None

    def get_today_date_string(self) -> str:
        """Get today's date as string"""
        try:
            today_dt = DT.now(TZ.utc)
            today_date = today_dt.date()
            today_string = today_date.isoformat()
            logger.info(f"📅 Today's date: {today_string}")
            return today_string
        except Exception as e:
            logger.error(f"❌ Error getting today's date: {e}")
            raise

    def get_scheduled_sessions_for_today(self) -> List[ScheduledSession]:
        """Get all sessions scheduled for today"""
        sessions = []
        
        try:
            target_date = self.get_today_date_string()
            logger.info(f"🔍 Looking for sessions on: {target_date}")
            
            response = self.supabase.table('courses').select(
                'id, title, teacher_id, start_date, nextsession, start_time, zoomLink'
            ).or_(
                f'start_date.eq.{target_date},nextsession.eq.{target_date}'
            ).execute()
            
            logger.info(f"📊 Database query completed. Found {len(response.data) if response.data else 0} courses")
            
            if not response.data:
                logger.info(f"📅 No courses found for {target_date}")
                return sessions
            
            for i, course in enumerate(response.data):
                logger.info(f"🔄 Processing course {i+1}: {course.get('title', 'Unknown')}")
                
                try:
                    session_type = 'new_course' if course.get('start_date') == target_date else 'continuing'
                    
                    zoom_link = course.get('zoomLink', '').strip()
                    if not zoom_link:
                        logger.warning(f"  ⚠️ No Zoom link found, skipping")
                        continue
                    
                    # Extract meeting info
                    meeting_id, password = self.extract_meeting_info_from_url(zoom_link)
                    if not meeting_id:
                        logger.warning(f"  ⚠️ Could not extract meeting ID, skipping")
                        continue
                    
                    agent_name = self.get_agent_name_for_teacher(course['teacher_id'])
                    if not agent_name:
                        agent_name = "AI Teaching Assistant"
                    
                    start_time_str = course.get('start_time', '')
                    if not start_time_str:
                        logger.warning(f"  ⚠️ No start_time found, skipping")
                        continue
                    
                    start_time_dt = self.create_start_datetime(target_date, start_time_str)
                    if not start_time_dt:
                        logger.warning(f"  ⚠️ Failed to create start datetime, skipping")
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
                    logger.info(f"  ✅ Successfully created session")
                    
                except Exception as e:
                    logger.error(f"  ❌ Error processing course {course.get('id', 'Unknown')}: {e}")
                    continue
            
            logger.info(f"📊 Total sessions found: {len(sessions)}")
            return sessions
            
        except Exception as e:
            logger.error(f"❌ Failed to get scheduled sessions: {e}")
            return sessions

    def create_start_datetime(self, date_str: str, time_str: str) -> Optional[DT]:
        """Create a datetime object from date and time strings"""
        try:
            time_parts = time_str.split(':')
            hour = int(time_parts[0])
            minute = int(time_parts[1])
            second = int(time_parts[2]) if len(time_parts) > 2 else 0
            
            target_dt = DT.fromisoformat(date_str)
            target_date = target_dt.date()
            
            from datetime import time as TIME_CLASS
            combined_dt = DT.combine(target_date, TIME_CLASS(hour, minute, second))
            final_dt = combined_dt.replace(tzinfo=TZ.utc)
            
            return final_dt
            
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
            return None
            
        except Exception as e:
            logger.error(f"❌ Error getting agent name for teacher {teacher_id}: {e}")
            return None

    def should_join_now(self, session: ScheduledSession) -> bool:
        """Check if it's time to join"""
        current_time = DT.now(TZ.utc)
        time_until_start = (session.start_time - current_time).total_seconds()
        
        # Join exactly at start time (with small buffer)
        should_join = -30 <= time_until_start <= 0
        
        logger.info(f"⏰ Join check for {session.course_title}:")
        logger.info(f"  Current time: {current_time}")
        logger.info(f"  Session start: {session.start_time}")
        logger.info(f"  Time until start: {time_until_start:.1f} seconds")
        logger.info(f"  Should join: {should_join}")
        
        return should_join

    def join_meeting_with_sdk(self, session: ScheduledSession) -> bool:
        """Join meeting using Zoom Meeting SDK (Web SDK approach)"""
        logger.info(f"🎯 JOINING MEETING WITH SDK: {session.course_title}")
        logger.info(f"👤 Agent: {session.agent_name}")
        logger.info(f"🔢 Meeting ID: {session.meeting_id}")
        
        try:
            # Generate SDK JWT token with role parameter
            sdk_jwt = self.generate_sdk_jwt(session.meeting_id, role=0)
            if not sdk_jwt:
                return False
            
            # For this example, we'll create an HTML file that uses the Zoom Web SDK
            # This is because the Python SDK requires complex native dependencies
            filename = self.create_web_sdk_join_page(session, sdk_jwt)
            
            logger.info("✅ Created Web SDK join page")
            logger.info(f"🌐 Meeting join page: {filename}")
            
            return True
            
        except Exception as e:
            logger.error(f"❌ Failed to join with SDK: {e}")
            import traceback
            logger.error(f"Full traceback: {traceback.format_exc()}")
            return False

    def create_web_sdk_join_page(self, session: ScheduledSession, sdk_jwt: str):
        """Create HTML page with latest Zoom Web SDK and better debugging"""
        html_content = f"""
<!DOCTYPE html>
<html>
<head>
    <title>Zoom SDK Join - {session.agent_name}</title>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <style>
        body {{
            font-family: Arial, sans-serif;
            padding: 20px;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            min-height: 100vh;
            margin: 0;
        }}
        
        #status {{
            position: fixed;
            top: 20px;
            left: 20px;
            background: rgba(0,0,0,0.9);
            padding: 20px;
            border-radius: 10px;
            z-index: 9999;
            max-width: 500px;
            max-height: 80vh;
            overflow-y: auto;
        }}
        
        #zmmtg-root {{
            width: 100%;
            height: 100vh;
        }}
        
        h1 {{
            margin: 0 0 10px 0;
            font-size: 24px;
            color: #4CAF50;
        }}
        
        p {{
            margin: 5px 0;
            font-size: 14px;
        }}
        
        #join-status {{
            font-weight: bold;
            font-size: 16px;
            color: #FFD700;
            margin: 10px 0;
        }}
        
        .debug-info {{
            background: rgba(255,255,255,0.1);
            padding: 10px;
            border-radius: 5px;
            margin: 10px 0;
            font-family: monospace;
            font-size: 12px;
        }}
        
        #error-log {{
            background: rgba(255,0,0,0.2);
            padding: 10px;
            border-radius: 5px;
            margin-top: 20px;
            display: block;
        }}
        
        #error-details {{
            font-size: 11px;
            white-space: pre-wrap;
            max-height: 300px;
            overflow-y: auto;
            font-family: monospace;
        }}
        
        .success {{
            color: #4CAF50;
            font-weight: bold;
        }}
        
        .error {{
            color: #ff6b6b;
            font-weight: bold;
        }}
        
        #manual-join {{
            margin-top: 20px;
            padding: 15px;
            background: rgba(0,100,200,0.2);
            border-radius: 5px;
        }}
        
        #manual-join button {{
            background: #4CAF50;
            color: white;
            border: none;
            padding: 10px 20px;
            border-radius: 5px;
            cursor: pointer;
            margin: 5px;
        }}
    </style>
</head>
<body>
    <div id="zmmtg-root"></div>
    <div id="status">
        <h1>{session.agent_name}</h1>
        <p><strong>Course:</strong> {session.course_title}</p>
        <p><strong>Meeting ID:</strong> {session.meeting_id}</p>
        <p><strong>Password:</strong> {'Yes' if session.password else 'No'}</p>
        
        <div class="debug-info">
            <strong>SDK Configuration:</strong><br>
            Key: {self.zoom_sdk_key}<br>
            Signature Length: {len(sdk_jwt)}<br>
            Time: {{new Date().toLocaleString()}}
        </div>
        
        <div id="join-status">Initializing...</div>
        
        <div id="error-log">
            <h4>Debug Log:</h4>
            <pre id="error-details"></pre>
        </div>
        
        <div id="manual-join">
            <h4>Manual Join Options:</h4>
            <button onclick="testSDKLoad()">Test SDK Load</button>
            <button onclick="initializeSDK()">Retry Initialize</button>
            <button onclick="openZoomApp()">Open Zoom App</button>
        </div>
    </div>

    <script>
        let joinInProgress = false;
        let sdkLoadAttempts = 0;
        const maxLoadAttempts = 3;
        
        function log(message, isError = false) {{
            console.log(message);
            const statusEl = document.getElementById('join-status');
            const errorDetails = document.getElementById('error-details');
            
            statusEl.innerHTML = message;
            errorDetails.textContent += new Date().toLocaleTimeString() + ': ' + message + '\\n';
            errorDetails.scrollTop = errorDetails.scrollHeight;
            
            if (isError) {{
                statusEl.classList.add('error');
            }} else {{
                statusEl.classList.remove('error');
            }}
        }}

        function testSDKLoad() {{
            log('Testing SDK load...');
            log('ZoomMtg type: ' + typeof ZoomMtg);
            log('Window location: ' + window.location.href);
            
            if (typeof ZoomMtg !== 'undefined') {{
                log('SDK is loaded! Available methods: ' + Object.keys(ZoomMtg).join(', '));
                initializeSDK();
            }} else {{
                log('SDK not loaded. Trying alternative load method...', true);
                loadSDKAlternative();
            }}
        }}

        function loadSDKAlternative() {{
            if (sdkLoadAttempts >= maxLoadAttempts) {{
                log('Max load attempts reached. SDK may be blocked.', true);
                return;
            }}
            
            sdkLoadAttempts++;
            log('SDK load attempt ' + sdkLoadAttempts + '/' + maxLoadAttempts);
            
            const script = document.createElement('script');
            script.onload = function() {{
                log('SDK script loaded successfully');
                setTimeout(() => {{
                    testSDKLoad();
                }}, 1000);
            }};
            script.onerror = function() {{
                log('Failed to load SDK script', true);
                setTimeout(() => {{
                    loadSDKAlternative();
                }}, 2000);
            }};
            
            // Try different SDK versions
            const sdkUrls = [
                'https://source.zoom.us/zoom-meeting-3.8.5.min.js',
                'https://source.zoom.us/3.8.5/lib/vendor/zoom-meeting-3.8.5.min.js',
                'https://dmogdx0jrul3u.cloudfront.net/3.8.5/lib/vendor/zoom-meeting-3.8.5.min.js'
            ];
            
            script.src = sdkUrls[(sdkLoadAttempts - 1) % sdkUrls.length];
            log('Loading SDK from: ' + script.src);
            document.head.appendChild(script);
        }}

        function initializeSDK() {{
            if (typeof ZoomMtg === 'undefined') {{
                log('ERROR: Zoom SDK not loaded', true);
                loadSDKAlternative();
                return;
            }}

            log('Zoom SDK loaded successfully');
            
            try {{
                log('Setting up Zoom SDK...');
                
                // Initialize SDK with proper options
                ZoomMtg.setZoomJSLib('https://source.zoom.us/lib', '/av');
                ZoomMtg.preLoadWasm();
                ZoomMtg.prepareWebSDK();
                
                log('SDK prepared, initializing...');
                
                // Initialize
                ZoomMtg.init({{
                    leaveUrl: window.location.origin,
                    disablePreview: false,
                    disableInvite: true,
                    disableCallOut: true,
                    disableRecord: true,
                    disableJoinAudio: false,
                    audioPanelAlwaysOpen: false,
                    showMeetingTime: true,
                    
                    success: function(initResult) {{
                        console.log('SDK Init Success:', initResult);
                        log('SDK initialized successfully! Joining meeting...');
                        joinMeeting();
                    }},
                    
                    error: function(initError) {{
                        console.error('SDK Init Error:', initError);
                        log('SDK initialization failed: ' + JSON.stringify(initError), true);
                        
                        if (initError && initError.errorMessage) {{
                            log('Init Error Details: ' + initError.errorMessage, true);
                        }}
                        if (initError && initError.errorCode) {{
                            log('Init Error Code: ' + initError.errorCode, true);
                        }}
                    }}
                }});
                
            }} catch (e) {{
                console.error('SDK Setup Exception:', e);
                log('SDK setup failed: ' + e.message, true);
            }}
        }}

        function joinMeeting() {{
            if (joinInProgress) {{
                log('Join already in progress');
                return;
            }}
            
            joinInProgress = true;
            log('Attempting to join meeting...');
            
            try {{
                const joinConfig = {{
                    signature: '{sdk_jwt}',
                    meetingNumber: '{session.meeting_id}',
                    userName: '{session.agent_name}',
                    apiKey: '{self.zoom_sdk_key}',
                    userEmail: 'agent@example.com',
                    passWord: '{session.password or ""}',
                    
                    success: function(joinResult) {{
                        console.log('Join Success:', joinResult);
                        log('Successfully joined meeting!', false);
                        document.getElementById('join-status').classList.add('success');
                        
                        setTimeout(() => {{
                            sendGreetingMessage();
                        }}, 5000);
                    }},
                    
                    error: function(joinError) {{
                        console.error('Join Error:', joinError);
                        joinInProgress = false;
                        
                        let errorMsg = 'Join failed';
                        if (joinError && joinError.errorMessage) {{
                            errorMsg += ': ' + joinError.errorMessage;
                        }}
                        if (joinError && joinError.errorCode) {{
                            errorMsg += ' (Code: ' + joinError.errorCode + ')';
                        }}
                        
                        log(errorMsg, true);
                        log('Full error: ' + JSON.stringify(joinError), true);
                        
                        // Common error codes
                        if (joinError && joinError.errorCode) {{
                            switch(joinError.errorCode) {{
                                case 3001: log('Meeting not found or ended', true); break;
                                case 3002: log('Meeting not started', true); break;
                                case 3003: log('Meeting locked by host', true); break;
                                case 3004: log('Invalid meeting password', true); break;
                                case 3005: log('Meeting capacity reached', true); break;
                                case 1: log('SDK signature verification failed', true); break;
                                default: log('Unknown error code: ' + joinError.errorCode, true);
                            }}
                        }}
                    }}
                }};
                
                console.log('Join config:', joinConfig);
                log('Calling ZoomMtg.join()...');
                
                ZoomMtg.join(joinConfig);
                
            }} catch (e) {{
                console.error('Join Exception:', e);
                joinInProgress = false;
                log('Join exception: ' + e.message, true);
            }}
        }}

        function sendGreetingMessage() {{
            try {{
                const message = 'Hello! {session.agent_name} has joined the session and is ready to assist.';
                
                if (typeof ZoomMtg.sendChat === 'function') {{
                    ZoomMtg.sendChat({{
                        text: message,
                        success: function() {{
                            log('Greeting message sent!');
                        }},
                        error: function(chatError) {{
                            log('Chat failed: ' + JSON.stringify(chatError));
                        }}
                    }});
                }} else {{
                    log('Chat function not available');
                }}
                
            }} catch (e) {{
                log('Chat exception: ' + e.message);
            }}
        }}

        function openZoomApp() {{
            const zoomUrl = 'zoommtg://zoom.us/join?confno={session.meeting_id}&pwd={session.password or ""}';
            log('Opening Zoom app with URL: ' + zoomUrl);
            window.location.href = zoomUrl;
        }}

        // Initialize when page loads
        log('Page loaded, starting initialization...');
        setTimeout(() => {{
            testSDKLoad();
        }}, 1000);
    </script>
</body>
</html>
        """
        
        # Save HTML file with timestamp
        import time
        filename = f"zoom_join_{session.course_id}_{int(time.time())}.html"
        filepath = os.path.abspath(filename)
        
        with open(filename, 'w', encoding='utf-8') as f:
            f.write(html_content)
        
        logger.info(f"Saved join page: {filename}")
        logger.info(f"File path: {filepath}")
        
        # Open the file in browser
        try:
            import webbrowser
            webbrowser.open(f'file://{filepath}')
            logger.info("Opened browser successfully")
        except Exception as e:
            logger.error(f"Failed to open browser: {e}")
        
        return filename

    def run_agent(self):
        """Main agent loop using SDK with better session handling"""
        logger.info("Starting Zoom SDK Agent...")
        
        self.is_active = True
        processed_sessions = set()
        last_join_attempt = {}  # Track last join attempt for each session
        
        try:
            while self.is_active:
                current_time = DT.now(TZ.utc)
                logger.info(f"Checking for sessions at {current_time}")
                
                sessions = self.get_scheduled_sessions_for_today()
                
                if not sessions:
                    logger.info("No sessions found for today")
                else:
                    logger.info(f"Found {len(sessions)} sessions for today")
                
                for session in sessions:
                    session_key = f"{session.course_id}_{session.start_time.isoformat()}"
                    
                    # Skip if already successfully processed
                    if session_key in processed_sessions:
                        continue
                    
                    if self.should_join_now(session):
                        logger.info(f"TIME TO JOIN: {session.course_title}")
                        
                        # Check if we've tried recently (prevent spam)
                        last_attempt = last_join_attempt.get(session_key, 0)
                        current_timestamp = TIME_MODULE.time()
                        
                        if current_timestamp - last_attempt < 60:  # Wait 60 seconds between attempts
                            logger.info("Join attempted recently, skipping...")
                            continue
                        
                        # Attempt to join
                        last_join_attempt[session_key] = current_timestamp
                        success = self.join_meeting_with_sdk(session)
                        
                        if success:
                            logger.info("Successfully initiated SDK join")
                            # Mark as processed after successful join
                            processed_sessions.add(session_key)
                        else:
                            logger.error("Failed to join with SDK")
                            # Don't mark as processed, will retry later
                        
                        self.current_session = session
                        
                    else:
                        time_until = (session.start_time - current_time).total_seconds()
                        if time_until > 0:
                            logger.info(f"{session.course_title} starts in {time_until/60:.1f} minutes")
                        elif time_until < -300:  # 5 minutes past start time
                            # Mark very old sessions as processed
                            logger.info(f"Session {session.course_title} is too old, marking as processed")
                            processed_sessions.add(session_key)
                
                TIME_MODULE.sleep(self.check_interval_seconds)
        
        except KeyboardInterrupt:
            logger.info("Agent stopped by user")
        except Exception as e:
            logger.error(f"Agent error: {e}")
            import traceback
            logger.error(f"Full traceback: {traceback.format_exc()}")
        finally:
            logger.info("Agent shutdown complete")

def main():
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s',
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler('zoom_sdk_agent.log')
        ]
    )
    
    print("🤖 Zoom Meeting SDK Agent")
    print("=" * 50)
    print("✅ Uses official Zoom Meeting SDK")
    print("✅ More reliable than browser automation") 
    print("✅ Better audio/video control")
    print("✅ No popup issues")
    print("=" * 50)
    
    try:
        agent = ZoomSDKAgent()
        agent.run_agent()
    except KeyboardInterrupt:
        print("\n👋 Goodbye!")
    except Exception as e:
        logger.error(f"❌ Failed to start agent: {e}")
        print(f"❌ Error: {e}")

if __name__ == "__main__":
    main()