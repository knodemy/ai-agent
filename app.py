# Enhanced app.py with automated lecture generation and Zoom SDK integration
import os
import io
import json
import logging
import time
from datetime import datetime, timedelta, date
from typing import Optional, List, Dict

from fastapi import FastAPI, HTTPException, UploadFile, File, Form, Body, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi import BackgroundTasks
from pydantic import BaseModel, Field
from supabase import create_client
import jwt
import threading  # ==== AGENT AUTO-JOIN (NEW) ====

# APScheduler imports
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
import asyncio

# ==== AGENT AUTO-JOIN (NEW) ====
# Playwright to open a lightweight browser as the agent participant
try:
    from playwright.sync_api import sync_playwright, Browser, Page
except Exception as _e:
    sync_playwright = None
    Browser = Page = None

# Your existing imports
try:
    from src.integrations.supabase_client import SupabaseClient
except Exception:
    from supabase_client import SupabaseClient

try:
    from src.core.content_processor import ContentProcessor
except Exception:
    try:
        from content_processor import ContentProcessor
    except Exception:
        ContentProcessor = None

try:
    from src.core.speech_generator import EnhancedTimedSpeechGenerator
    SpeechGenerator = EnhancedTimedSpeechGenerator
    TimedSpeechGenerator = EnhancedTimedSpeechGenerator
except Exception:
    try:
        from speech_generator import EnhancedTimedSpeechGenerator
        SpeechGenerator = EnhancedTimedSpeechGenerator
        TimedSpeechGenerator = EnhancedTimedSpeechGenerator
    except Exception as e:
        print(f"Failed to import EnhancedTimedSpeechGenerator: {e}")
        SpeechGenerator = None
        TimedSpeechGenerator = None

logger = logging.getLogger("api")
logging.basicConfig(level=logging.INFO)

app = FastAPI(title="Automated Teacher Lectures API")
ZOOM_SDK_VERSION = "3.8.5"

# CORS configuration
origins = [
    "http://localhost:8080",
    "http://localhost:3000",
    "http://127.0.0.1:8080",
    "http://127.0.0.1:3000",
    "https://teacher.knodemy.ai",
    "https://devteacher.knodemy.ai",
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_origin_regex=r"^https://([a-z0-9-]+\.)?knodemy\.ai$",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Settings
SCRIPTS_BUCKET = os.getenv("SCRIPTS_BUCKET", "lecture-scripts")
AUDIO_BUCKET = os.getenv("AUDIO_BUCKET", "lecture-audios")
SIGN_URLS = os.getenv("SIGN_URLS", "true").lower() == "true"
SIGN_EXPIRES_SECONDS = int(os.getenv("SIGN_EXPIRES_SECONDS", "3600"))
GENERATE_TIMED_AUDIO = os.getenv("GENERATE_TIMED_AUDIO", "true").lower() == "true"

# Zoom SDK configuration
ZOOM_SDK_KEY = os.getenv("ZOOM_SDK_KEY")
ZOOM_SDK_SECRET = os.getenv("ZOOM_SDK_SECRET")

# ==== AGENT AUTO-JOIN (NEW) ====
# Base URL this FastAPI app is reachable on, so Playwright can open /zoom/join
AGENT_JOIN_BASE_URL = os.getenv("AGENT_JOIN_BASE_URL", "http://127.0.0.1:8000")

# Validate Zoom credentials
if not ZOOM_SDK_KEY or not ZOOM_SDK_SECRET:
    logger.warning("Missing ZOOM_SDK_KEY/ZOOM_SDK_SECRET - Zoom signature generation will be disabled")

# Initialize scheduler
scheduler = AsyncIOScheduler()

# Pydantic models
class SignatureReq(BaseModel):
    meetingNumber: str  # e.g., "123456789"
    role: int  # 0=attendee, 1=host

# Helper functions
def get_tomorrow_date() -> str:
    """Get tomorrow's date in YYYY-MM-DD format based on UTC"""
    tomorrow = datetime.utcnow() + timedelta(days=1)
    return tomorrow.strftime('%Y-%m-%d')

def get_today_date() -> str:
    """Get today's date in YYYY-MM-DD format based on UTC"""
    return datetime.utcnow().strftime('%Y-%m-%d')

def _build_bucket_path(teacher_id: str, course_id: str, lesson_id: str, date: str, ext: str = "pdf") -> str:
    """Build structured bucket path with date"""
    return f"{teacher_id}/{course_id}/{date}/{lesson_id}_script.{ext}"

async def get_courses_for_target_date(target_date: str) -> List[dict]:
    """
    Get all courses that need lecture generation for the target date.
    """
    try:
        url = os.getenv("SUPABASE_URL")
        key = os.getenv("SUPABASE_KEY")
        supabase = create_client(url, key)

        response = supabase.table('courses').select(
            'id, title, teacher_id, start_date, nextsession, start_time, end_time'
        ).or_(f'start_date.eq.{target_date},nextsession.eq.{target_date}').execute()

        courses = response.data or []

        new_courses = [c for c in courses if c.get('start_date') == target_date]
        continuing_courses = [c for c in courses if c.get('nextsession') == target_date and c.get('start_date') != target_date]

        logger.info(f"Found courses for {target_date}:")
        logger.info(f"  - New courses starting: {len(new_courses)}")
        logger.info(f"  - Continuing courses with next session: {len(continuing_courses)}")
        logger.info(f"  - Total courses to process: {len(courses)}")
        return courses

    except Exception as e:
        logger.error(f"Error fetching courses for {target_date}: {e}")
        return []

async def check_if_lecture_already_generated(teacher_id: str, course_id: str, target_date: str) -> bool:
    """Check if lectures have already been generated for this course on the target date"""
    try:
        url = os.getenv("SUPABASE_URL")
        key = os.getenv("SUPABASE_KEY")
        supabase = create_client(url, key)
        response = supabase.table('prepared_lessons').select(
            'lesson_id, url, audio_url, created_at'
        ).eq('teacher_id', teacher_id).execute()
        if response.data:
            for lesson in response.data:
                if lesson.get('url') and target_date in lesson['url']:
                    logger.info(f"Lectures already exist for teacher {teacher_id}, course {course_id} on {target_date}")
                    return True
        return False
    except Exception as e:
        logger.error(f"Error checking existing lectures: {e}")
        return False

async def process_course_for_automated_generation(course: dict, target_date: str) -> dict:
    """Process a single course to generate scripts and audio for the target date"""
    course_id = course['id']
    teacher_id = course['teacher_id']
    course_title = course['title']

    result = {
        'course_id': course_id,
        'teacher_id': teacher_id,
        'course_title': course_title,
        'target_date': target_date,
        'lessons_processed': 0,
        'successful_generations': 0,
        'failed_generations': 0,
        'successful_audio_generations': 0,
        'failed_audio_generations': 0,
        'errors': [],
        'skipped_reason': None
    }

    try:
        client = SupabaseClient(teacher_id=teacher_id)
        lessons_with_pdfs = client.get_lessons_with_pdf_resources(course_id)
        result['lessons_processed'] = len(lessons_with_pdfs)

        if not lessons_with_pdfs:
            result['skipped_reason'] = 'No lessons with PDF resources found'
            logger.info(f"No lessons with PDFs found for course {course_id}")
            return result

        if not ContentProcessor:
            raise Exception("ContentProcessor not available")
        cp = ContentProcessor()

        teacher_info = client.get_teacher_info()
        teacher_name = teacher_info.get('name', 'Teacher')

        for lesson in lessons_with_pdfs:
            lesson_id = lesson['id']
            lesson_title = lesson.get('title', f'Lesson {lesson_id}')
            try:
                for idx, pdf_url in enumerate(lesson.get('pdf_urls', []), start=1):
                    try:
                        logger.info(f"Generating script for lesson {lesson_id}, PDF {idx} for {target_date}")
                        script_pack = cp.generate_script_pdf_bytes(
                            pdf_source_url=pdf_url,
                            lesson_title=lesson_title,
                            teacher_name=teacher_name,
                            audience="middle school (ages 11-14)",
                            language="English"
                        )
                        bucket_path = _build_bucket_path(teacher_id, course_id, lesson_id, target_date, ext="pdf")
                        client.upload_pdf_to_bucket(
                            bucket=SCRIPTS_BUCKET,
                            pdf_bytes=script_pack["pdf_bytes"],
                            path=bucket_path,
                            upsert=True
                        )
                        file_url = client.create_signed_url(SCRIPTS_BUCKET, bucket_path, expires_in=SIGN_EXPIRES_SECONDS) if SIGN_URLS \
                                   else client.get_public_url(SCRIPTS_BUCKET, bucket_path)
                        if file_url:
                            client.record_prepared_lesson(lesson_id, file_url)

                        result['successful_generations'] += 1
                        logger.info(f"Successfully generated script for lesson {lesson_id}")

                        if file_url and GENERATE_TIMED_AUDIO and TimedSpeechGenerator:
                            try:
                                logger.info(f"Generating audio for lesson {lesson_id}")
                                timed_speech_gen = TimedSpeechGenerator()
                                audio_result = timed_speech_gen.generate_timed_lesson_audio(
                                    teacher_id=teacher_id,
                                    course_id=course_id,
                                    lesson_id=lesson_id,
                                    lesson_title=lesson_title,
                                    script_url=file_url,
                                    date=target_date,
                                    voice="alloy"
                                )
                                if audio_result['success']:
                                    result['successful_audio_generations'] += 1
                                    logger.info(f"Successfully generated audio for lesson {lesson_id} ({audio_result.get('duration_minutes', 0)} min)")
                                else:
                                    result['failed_audio_generations'] += 1
                                    result['errors'].append({
                                        'lesson_id': lesson_id,
                                        'type': 'audio_generation',
                                        'error': audio_result.get('error', 'Unknown audio error')
                                    })
                            except Exception as audio_error:
                                result['failed_audio_generations'] += 1
                                result['errors'].append({
                                    'lesson_id': lesson_id,
                                    'type': 'audio_generation',
                                    'error': str(audio_error)
                                })
                    except Exception as pdf_error:
                        result['failed_generations'] += 1
                        result['errors'].append({
                            'lesson_id': lesson_id,
                            'pdf_index': idx,
                            'type': 'script_generation',
                            'error': str(pdf_error)
                        })
            except Exception as lesson_error:
                result['failed_generations'] += 1
                result['errors'].append({
                    'lesson_id': lesson_id,
                    'type': 'lesson_processing',
                    'error': str(lesson_error)
                })
        return result
    except Exception as course_error:
        result['errors'].append({
            'course_id': course_id,
            'type': 'course_processing',
            'error': str(course_error)
        })
        logger.error(f"Course processing failed for course {course_id}: {course_error}")
        return result

async def generate_lectures_for_date(target_date: str):
    """Main function to generate lectures and audio for all courses on the target date"""
    start_time = datetime.now()
    logger.info(f"Starting automated lecture generation for {target_date} at {start_time}")
    try:
        courses = await get_courses_for_target_date(target_date)
        if not courses:
            logger.info(f"No courses found for {target_date}")
            return
        total_successful = total_failed = total_lessons = 0
        total_successful_audio = total_failed_audio = 0
        all_errors = []
        skipped_courses = 0
        for course in courses:
            result = await process_course_for_automated_generation(course, target_date)
            if result.get('skipped_reason'):
                skipped_courses += 1
                logger.info(f"Skipped course {course['id']}: {result['skipped_reason']}")
                continue
            total_lessons += result['lessons_processed']
            total_successful += result['successful_generations']
            total_failed += result['failed_generations']
            total_successful_audio += result['successful_audio_generations']
            total_failed_audio += result['failed_audio_generations']
            all_errors.extend(result['errors'])
        end_time = datetime.now()
        duration = (end_time - start_time).total_seconds()
        summary = {
            'target_date': target_date,
            'total_courses_found': len(courses),
            'skipped_courses': skipped_courses,
            'processed_courses': len(courses) - skipped_courses,
            'total_lessons_processed': total_lessons,
            'successful_script_generations': total_successful,
            'failed_script_generations': total_failed,
            'successful_audio_generations': total_successful_audio,
            'failed_audio_generations': total_failed_audio,
            'duration_seconds': duration,
            'errors': all_errors
        }
        logger.info(f"Automated lecture generation for {target_date} completed: {summary}")
        await store_generation_summary(summary)
    except Exception as e:
        logger.error(f"Automated lecture generation for {target_date} failed: {e}")

async def store_generation_summary(summary: dict):
    try:
        url = os.getenv("SUPABASE_URL")
        key = os.getenv("SUPABASE_KEY")
        supabase = create_client(url, key)
        logger.info(f"Generation summary stored: {json.dumps(summary, indent=2)}")
    except Exception as e:
        logger.error(f"Failed to store generation summary: {e}")

# SCHEDULED JOBS
@scheduler.scheduled_job('cron', hour=5, minute=0, timezone='UTC')  # Run at 05:00 UTC daily
async def scheduled_daily_lecture_generation():
    today = get_today_date()
    logger.info(f"Running scheduled lecture generation for {today}")
    await generate_lectures_for_date(today)

@app.get("/zoom/join", response_class=HTMLResponse)
async def zoom_join_page(
    mn: str = Query(..., description="Meeting number"),
    pwd: str = Query("", description="Passcode (optional)"),
    name: str = Query("AI Teaching Assistant"),
    role: int = Query(0, description="0 attendee, 1 host"),
    leave: str = Query("https://knodemy.ai")
):
    try:
        signature = generate_meeting_sdk_signature(mn, role)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Signature error: {e}")

    host_zak_js = "undefined"  # replace if you add host ZAK
    html = f"""<!doctype html>
<html>
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width,initial-scale=1"/>
  <title>Zoom SDK Join</title>
  <link rel="stylesheet" href="https://source.zoom.us/{ZOOM_SDK_VERSION}/css/bootstrap.css"/>
  <link rel="stylesheet" href="https://source.zoom.us/{ZOOM_SDK_VERSION}/css/react-select.css"/>
  <style>
    body {{ margin:0; font-family:system-ui, Arial; background: #f5f5f5; }}
    #zmmtg-root, #aria-notify-area {{ height: 100vh; }}
    .loading {{ text-align: center; padding: 50px; font-size: 18px; }}
  </style>
</head>
<body>
  <div id="loading-message" class="loading">
    <h2>Loading Zoom SDK...</h2>
    <p>Please wait while we initialize the meeting.</p>
  </div>
  <div id="zmmtg-root" style="display: none;"></div>
  <div id="aria-notify-area"></div>

  <script>
    let dependenciesLoaded = 0;
    const totalDependencies = 3;
    function checkAllLoaded() {{
      dependenciesLoaded++;
      if (dependenciesLoaded >= totalDependencies) {{
        initZoom();
      }}
    }}
    function loadScript(src, callback) {{
      const script = document.createElement('script');
      script.src = src;
      script.onload = callback;
      script.onerror = () => console.error('Failed to load:', src);
      document.head.appendChild(script);
    }}
    loadScript('https://cdnjs.cloudflare.com/ajax/libs/jquery/3.6.0/jquery.min.js', checkAllLoaded);
    loadScript('https://cdnjs.cloudflare.com/ajax/libs/lodash.js/4.17.21/lodash.min.js', checkAllLoaded);
    loadScript('https://source.zoom.us/zoom-meeting-{ZOOM_SDK_VERSION}.min.js', checkAllLoaded);

    function initZoom() {{
      document.getElementById('loading-message').style.display = 'none';
      document.getElementById('zmmtg-root').style.display = 'block';

      if (typeof ZoomMtg === 'undefined') {{
        console.error('ZoomMtg not loaded');
        document.getElementById('loading-message').innerHTML = '<h2>Error</h2><p>Failed to load Zoom SDK</p>';
        document.getElementById('loading-message').style.display = 'block';
        return;
      }}

      const signature     = {json.dumps(signature)};
      const sdkKey        = {json.dumps(ZOOM_SDK_KEY)};
      const meetingNumber = {json.dumps(mn)};
      const passcode      = {json.dumps(pwd or "")};
      const userName      = {json.dumps(name)};
      const leaveUrl      = {json.dumps(leave)};
      const role          = {json.dumps(role)};

      ZoomMtg.setZoomJSLib("https://source.zoom.us/{ZOOM_SDK_VERSION}/lib", "/av");
      ZoomMtg.preLoadWasm();
      ZoomMtg.prepareWebSDK();

      ZoomMtg.init({{
        leaveUrl,
        success: () => {{
          ZoomMtg.join({{
            signature,
            sdkKey,
            meetingNumber,
            userName,
            passWord: passcode,
            success: () => {{
              console.log("Successfully joined meeting");
            }},
            error: (err) => {{
              console.error("Join error:", err);
              alert('Failed to join meeting: ' + (err.reason || 'Unknown error'));
            }}
          }});
        }},
        error: (err) => {{
          console.error("Init error:", err);
          alert('Failed to initialize Zoom: ' + (err.reason || 'Unknown error'));
        }}
      }});
    }}
  </script>
</body>
</html>"""
    return HTMLResponse(content=html, status_code=200)

# LECTURE GENERATION API ENDPOINTS
@app.post("/lectures/generate-for-date")
async def generate_lectures_for_specific_date(target_date: str = Query(...)):
    try:
        await generate_lectures_for_date(target_date)
        return {"message": f"Lecture generation completed for {target_date}"}
    except Exception as e:
        logger.error(f"Manual lecture generation failed for {target_date}: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/lectures/generate-today")
async def generate_lectures_today():
    today = get_today_date()
    try:
        await generate_lectures_for_date(today)
        return {"message": f"Lecture generation completed for today ({today})"}
    except Exception as e:
        logger.error(f"Manual lecture generation failed for today: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/lectures/generate-tomorrow")
async def generate_lectures_tomorrow():
    tomorrow = get_tomorrow_date()
    try:
        await generate_lectures_for_date(tomorrow)
        return {"message": f"Lecture generation completed for tomorrow ({tomorrow})"}
    except Exception as e:
        logger.error(f"Manual lecture generation failed for tomorrow: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/lectures/preview-date")
async def preview_courses_for_date(target_date: str = Query(...)):
    try:
        courses = await get_courses_for_target_date(target_date)
        course_details = []
        for course in courses:
            already_generated = await check_if_lecture_already_generated(
                course['teacher_id'], course['id'], target_date
            )
            course_details.append({
                'course_id': course['id'],
                'course_title': course['title'],
                'teacher_id': course['teacher_id'],
                'start_date': course.get('start_date'),
                'nextsession': course.get('nextsession'),
                'start_time': course.get('start_time'),
                'end_time': course.get('end_time'),
                'already_generated': already_generated,
                'reason': 'new_course' if course.get('start_date') == target_date else 'next_session'
            })
        return {
            'target_date': target_date,
            'courses_count': len(courses),
            'courses': course_details
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/debug/scheduler-status")
async def get_scheduler_status():
    jobs = scheduler.get_jobs()
    job_info = []
    for job in jobs:
        job_info.append({
            "id": job.id,
            "name": job.name,
            "next_run_time": str(job.next_run_time),
            "trigger": str(job.trigger)
        })
    return {
        "scheduler_running": scheduler.running,
        "jobs": job_info,
        "current_utc_time": datetime.utcnow().isoformat(),
        "today_date": get_today_date(),
        "tomorrow_date": get_tomorrow_date(),
        "automated_generation_enabled": True,
        "timed_audio_enabled": GENERATE_TIMED_AUDIO,
        "zoom_sdk_configured": bool(ZOOM_SDK_KEY and ZOOM_SDK_SECRET),
        "components_available": {
            "EnhancedTimedSpeechGenerator": TimedSpeechGenerator is not None,
            "ContentProcessor": ContentProcessor is not None,
            "ZoomSDK": bool(ZOOM_SDK_KEY and ZOOM_SDK_SECRET)
        }
    }

def generate_meeting_sdk_signature(meeting_number: str, role: int) -> str:
    """Generate JWT signature for Zoom Web SDK"""
    if not ZOOM_SDK_KEY or not ZOOM_SDK_SECRET:
        raise HTTPException(status_code=500, detail="Zoom SDK credentials not configured")
    try:
        iat = int(time.time())
        exp = iat + 60 * 2
        token_exp = iat + 60 * 60
        payload = {"sdkKey": ZOOM_SDK_KEY, "mn": meeting_number, "role": role, "iat": iat, "exp": exp, "tokenExp": token_exp}
        signature = jwt.encode(payload, ZOOM_SDK_SECRET, algorithm="HS256")
        return signature
    except Exception as e:
        logger.error(f"Error generating Zoom signature: {e}")
        raise HTTPException(status_code=500, detail=f"Signature generation failed: {e}")

# ZOOM API ENDPOINTS
@app.post("/zoom/signature")
def create_signature(body: SignatureReq):
    try:
        signature = generate_meeting_sdk_signature(body.meetingNumber, body.role)
        return {"signature": signature, "sdkKey": ZOOM_SDK_KEY}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/zoom/signature")
def create_signature_api(body: SignatureReq):
    try:
        signature = generate_meeting_sdk_signature(body.meetingNumber, body.role)
        return {"signature": signature, "sdkKey": ZOOM_SDK_KEY}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# =========================
# ==== AGENT AUTO-JOIN (NEW)
# =========================

# Keep track of one agent browser per meetingNumber
_agent_sessions_lock = threading.Lock()
_agent_sessions: Dict[str, Dict[str, object]] = {}

def _find_agent_name_by_teacher(teacher_id: Optional[str]) -> Optional[str]:
    """Read agent_instances(agent_name, current_teacher_id) and return agent_name."""
    if not teacher_id:
        return None
    try:
        url = os.getenv("SUPABASE_URL")
        key = os.getenv("SUPABASE_KEY")
        sb = create_client(url, key)
        resp = sb.table("agent_instances").select("agent_name").eq("current_teacher_id", teacher_id).limit(1).execute()
        if not resp.data:
            return None
        return resp.data[0]["agent_name"]
    except Exception as e:
        logger.error(f"Supabase lookup failed for teacher {teacher_id}: {e}")
        return None

def _launch_agent_browser(meeting_number: str, passcode: str, agent_name: str) -> None:
    """Open /zoom/join as the agent (role=0) using Playwright Chromium."""
    if sync_playwright is None:
        logger.error("Playwright not installed. Run: pip install playwright && playwright install chromium")
        return
    join_url = f"{AGENT_JOIN_BASE_URL}/zoom/join?mn={meeting_number}&pwd={passcode or ''}&name={agent_name}&role=0&leave=https%3A%2F%2Fknodemy.ai"
    logger.info(f"[agent] Opening join URL: {join_url}")

    with sync_playwright() as p:
        browser: Browser = p.chromium.launch(headless=True)
        page: Page = browser.new_page()

        with _agent_sessions_lock:
            _agent_sessions[str(meeting_number)] = {"browser": browser, "page": page}

        page.goto(join_url, wait_until="networkidle")
        # Keep this function blocked until closed from /api/agent/leave (optional TTL here)
        # page.wait_for_timeout(60 * 60 * 1000)

def _spawn_agent(meeting_number: str, passcode: str, agent_name: str, delay_ms: int = 3000) -> None:
    """Spawn a thread to join after a short delay."""
    def runner():
        try:
            time.sleep(delay_ms / 1000.0)
            _launch_agent_browser(meeting_number, passcode, agent_name)
        except Exception as e:
            logger.error(f"[agent] runner failed: {e}")
            with _agent_sessions_lock:
                _agent_sessions.pop(str(meeting_number), None)
    threading.Thread(target=runner, daemon=True).start()

def _close_agent(meeting_number: str) -> bool:
    """Close the Playwright browser/page for this meeting."""
    mn = str(meeting_number)
    with _agent_sessions_lock:
        sess = _agent_sessions.get(mn)
        if not sess:
            return False
        try:
            page: Page = sess.get("page")  # type: ignore
            if page:
                try:
                    page.close()
                except Exception:
                    pass
            browser: Browser = sess.get("browser")  # type: ignore
            if browser:
                try:
                    browser.close()
                except Exception:
                    pass
        finally:
            _agent_sessions.pop(mn, None)
    return True

@app.post("/api/agent/join")
async def agent_join(req: Request):
    data = await req.json()
    meeting_number = str(data.get("meetingNumber") or "").strip()
    passcode = str(data.get("passcode") or "")
    teacher_id = data.get("teacherId")

    print("[agent] /api/agent/join payload:", data)

    if not meeting_number:
        raise HTTPException(status_code=400, detail="meetingNumber required")
    if not teacher_id:
        raise HTTPException(status_code=400, detail="teacherId required")

    agent_name = _find_agent_name_by_teacher(teacher_id)
    print(f"[agent] agent_name found: {agent_name}") 
    if not agent_name:
        raise HTTPException(status_code=404, detail="No agent mapped to the current teacher")

    with _agent_sessions_lock:
        if meeting_number in _agent_sessions:
            return {"ok": True, "note": "agent already joined or joining"}

    _spawn_agent(meeting_number, passcode, agent_name, delay_ms=3000)
    return {"ok": True}

# @app.post("/api/agent/leave")
# async def agent_leave(req: Request):
#     """Close the agent's browser for this meeting. Body: { meetingNumber: string }"""
#     data = await req.json()
#     meeting_number = str(data.get("meetingNumber") or "").strip()
#     if not meeting_number:
#         return {"error": "meetingNumber required"}, 400
#     closed = _close_agent(meeting_number)
#     return {"ok": True, "closed": closed}

# App lifecycle events
@app.on_event("startup")
async def start_scheduler():
    scheduler.start()
    logger.info("APScheduler started successfully")
    logger.info("Automated lecture generation system initialized")
    logger.info(f"Scheduled jobs: midnight generation only")
    logger.info(f"Audio generation: {'enabled' if GENERATE_TIMED_AUDIO else 'disabled'}")
    logger.info(f"Zoom SDK: {'configured' if ZOOM_SDK_KEY and ZOOM_SDK_SECRET else 'not configured'}")
    if sync_playwright is None:
        logger.warning("Playwright not installed; agent auto-join will be disabled until installed.")

@app.on_event("shutdown")
async def shutdown_scheduler():
    scheduler.shutdown()
    logger.info("APScheduler shutdown complete")

# (Note: your /teacher/* endpoints appear twice in the original file; keeping one set avoids duplication.)

@app.get("/health")
async def health_check():
    return {"status": "healthy", "message": "API is running"}

@app.get("/")
async def root():
    return {"message": "Automated Teacher Lectures API", "status": "running"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
