# Enhanced app.py with automated lecture generation
import os
import io
import json
import logging
from datetime import datetime, timedelta, date
from typing import Optional, List, Dict

from fastapi import FastAPI, HTTPException, UploadFile, File, Form, Body, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi import BackgroundTasks
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field
from supabase import create_client

# APScheduler imports
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
import asyncio

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
    "https://devteacher.knodemy.ai"
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_origin_regex=r"^https://([a-z0-9-]+\.)?knodemy\.ai$",  #keeping this for production
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

# Initialize scheduler
scheduler = AsyncIOScheduler()

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
    This includes:
    1. New courses starting on target_date (start_date = target_date)
    2. Existing courses with next session on target_date (nextsession = target_date)
    """
    try:
        url = os.getenv("SUPABASE_URL")
        key = os.getenv("SUPABASE_KEY")
        supabase = create_client(url, key)
        
        # Get courses where start_date = target_date OR nextsession = target_date
        response = supabase.table('courses').select(
            'id, title, teacher_id, start_date, nextsession, start_time, end_time'
        ).or_(
            f'start_date.eq.{target_date},nextsession.eq.{target_date}'
        ).execute()
        
        courses = response.data or []
        
        # Log detailed info about what we found
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
    """
    Check if lectures have already been generated for this course on the target date
    """
    try:
        url = os.getenv("SUPABASE_URL")
        key = os.getenv("SUPABASE_KEY")
        supabase = create_client(url, key)
        
        # Check if there are any prepared_lessons for this teacher/course with URLs containing the target date
        response = supabase.table('prepared_lessons').select(
            'lesson_id, url, audio_url, created_at'
        ).eq('teacher_id', teacher_id).execute()
        
        if response.data:
            # Check if any URLs contain the target date (indicating they were generated for that date)
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
        # Check if lectures already exist for this date
        #already_generated = await check_if_lecture_already_generated(teacher_id, course_id, target_date)
        #if already_generated:
            #result['skipped_reason'] = 'Lectures already generated for this date'
            #logger.info(f"Skipping course {course_id} - lectures already generated for {target_date}")
            #return result
        
        client = SupabaseClient(teacher_id=teacher_id)
        
        # Get lessons with PDF resources for this course
        lessons_with_pdfs = client.get_lessons_with_pdf_resources(course_id)
        result['lessons_processed'] = len(lessons_with_pdfs)
        
        if not lessons_with_pdfs:
            result['skipped_reason'] = 'No lessons with PDF resources found'
            logger.info(f"No lessons with PDFs found for course {course_id}")
            return result
        
        # Initialize content processor
        if not ContentProcessor:
            raise Exception("ContentProcessor not available")
        
        cp = ContentProcessor()
        
        # Get teacher info for script generation
        teacher_info = client.get_teacher_info()
        teacher_name = teacher_info.get('name', 'Teacher')
        
        # Process each lesson
        for lesson in lessons_with_pdfs:
            lesson_id = lesson['id']
            lesson_title = lesson.get('title', f'Lesson {lesson_id}')
            
            try:
                # Process each PDF URL in the lesson
                for idx, pdf_url in enumerate(lesson.get('pdf_urls', []), start=1):
                    try:
                        logger.info(f"Generating script for lesson {lesson_id}, PDF {idx} for {target_date}")
                        
                        # Generate script PDF
                        script_pack = cp.generate_script_pdf_bytes(
                            pdf_source_url=pdf_url,
                            lesson_title=lesson_title,
                            teacher_name=teacher_name,
                            audience="middle school (ages 11-14)",
                            language="English"
                        )
                        
                        # Build bucket path with target date
                        bucket_path = _build_bucket_path(
                            teacher_id, course_id, lesson_id, target_date, ext="pdf"
                        )
                        
                        # Upload to bucket
                        client.upload_pdf_to_bucket(
                            bucket=SCRIPTS_BUCKET,
                            pdf_bytes=script_pack["pdf_bytes"],
                            path=bucket_path,
                            upsert=True
                        )
                        
                        # Get URL for database record
                        file_url = None
                        if SIGN_URLS:
                            file_url = client.create_signed_url(
                                SCRIPTS_BUCKET, bucket_path, expires_in=SIGN_EXPIRES_SECONDS
                            )
                        else:
                            file_url = client.get_public_url(SCRIPTS_BUCKET, bucket_path)
                        
                        # Record in prepared_lessons table
                        if file_url:
                            client.record_prepared_lesson(lesson_id, file_url)
                        
                        result['successful_generations'] += 1
                        logger.info(f"Successfully generated script for lesson {lesson_id}")
                        
                        # Generate audio if enabled
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
                                    logger.error(f"Failed to generate audio for lesson {lesson_id}: {audio_result.get('error')}")
                                        
                            except Exception as audio_error:
                                result['failed_audio_generations'] += 1
                                result['errors'].append({
                                    'lesson_id': lesson_id,
                                    'type': 'audio_generation',
                                    'error': str(audio_error)
                                })
                                logger.error(f"Audio generation exception for lesson {lesson_id}: {audio_error}")
                            
                    except Exception as pdf_error:
                        result['failed_generations'] += 1
                        result['errors'].append({
                            'lesson_id': lesson_id,
                            'pdf_index': idx,
                            'type': 'script_generation',
                            'error': str(pdf_error)
                        })
                        logger.error(f"Script generation failed for lesson {lesson_id}, PDF {idx}: {pdf_error}")
                        
            except Exception as lesson_error:
                result['failed_generations'] += 1
                result['errors'].append({
                    'lesson_id': lesson_id,
                    'type': 'lesson_processing',
                    'error': str(lesson_error)
                })
                logger.error(f"Lesson processing failed for lesson {lesson_id}: {lesson_error}")
        
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
    """
    Main function to generate lectures and audio for all courses on the target date
    """
    start_time = datetime.now()
    logger.info(f"Starting automated lecture generation for {target_date} at {start_time}")
    
    try:
        # Get all courses that need processing for the target date
        courses = await get_courses_for_target_date(target_date)
        
        if not courses:
            logger.info(f"No courses found for {target_date}")
            return
        
        # Process each course
        total_successful = 0
        total_failed = 0
        total_lessons = 0
        total_successful_audio = 0
        total_failed_audio = 0
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
        
        # Log summary
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
        
        # Store the summary in database for tracking (optional)
        await store_generation_summary(summary)
        
    except Exception as e:
        logger.error(f"Automated lecture generation for {target_date} failed: {e}")

async def store_generation_summary(summary: dict):
    """Store the generation summary in database for tracking purposes"""
    try:
        url = os.getenv("SUPABASE_URL")
        key = os.getenv("SUPABASE_KEY")
        supabase = create_client(url, key)
        
        # You might want to create a 'generation_logs' table for this
        # For now, just log it
        logger.info(f"Generation summary stored: {json.dumps(summary, indent=2)}")
        
    except Exception as e:
        logger.error(f"Failed to store generation summary: {e}")

# SCHEDULED JOBS
@scheduler.scheduled_job('cron', hour=5, minute=0, timezone='UTC')  # Run at midnight UTC
async def scheduled_daily_lecture_generation():
    """
    Scheduled job that runs at midnight UTC to generate lectures for the current day
    This ensures lectures are ready when the day begins
    """
    today = get_today_date()
    logger.info(f"Running scheduled lecture generation for {today}")
    await generate_lectures_for_date(today)

# API ENDPOINTS

@app.post("/lectures/generate-for-date")
async def generate_lectures_for_specific_date(target_date: str = Query(...)):
    """Manually trigger lecture generation for a specific date"""
    try:
        await generate_lectures_for_date(target_date)
        return {"message": f"Lecture generation completed for {target_date}"}
    except Exception as e:
        logger.error(f"Manual lecture generation failed for {target_date}: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/lectures/generate-today")
async def generate_lectures_today():
    """Manually trigger lecture generation for today"""
    today = get_today_date()
    try:
        await generate_lectures_for_date(today)
        return {"message": f"Lecture generation completed for today ({today})"}
    except Exception as e:
        logger.error(f"Manual lecture generation failed for today: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/lectures/generate-tomorrow")
async def generate_lectures_tomorrow():
    """Manually trigger lecture generation for tomorrow"""
    tomorrow = get_tomorrow_date()
    try:
        await generate_lectures_for_date(tomorrow)
        return {"message": f"Lecture generation completed for tomorrow ({tomorrow})"}
    except Exception as e:
        logger.error(f"Manual lecture generation failed for tomorrow: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/lectures/preview-date")
async def preview_courses_for_date(target_date: str = Query(...)):
    """Preview what courses will be processed for a specific date"""
    try:
        courses = await get_courses_for_target_date(target_date)
        
        course_details = []
        for course in courses:
            # Check if already generated
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
    """Debug endpoint to check scheduler status and next run times"""
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
        "components_available": {
            "EnhancedTimedSpeechGenerator": TimedSpeechGenerator is not None,
            "ContentProcessor": ContentProcessor is not None
        }
    }

# App lifecycle events
@app.on_event("startup")
async def start_scheduler():
    """Start the scheduler when the app starts"""
    scheduler.start()
    logger.info("APScheduler started successfully")
    logger.info("Automated lecture generation system initialized")
    logger.info(f"Scheduled jobs: midnight generation only")
    logger.info(f"Audio generation: {'enabled' if GENERATE_TIMED_AUDIO else 'disabled'}")

@app.on_event("shutdown")
async def shutdown_scheduler():
    """Gracefully shutdown the scheduler when the app stops"""
    scheduler.shutdown()
    logger.info("APScheduler shutdown complete")

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

    # If joining as host (role=1), you must also pass host ZAK to ZoomMtg.join({ zak: ... })
    host_zak_js = "undefined"  # replace with a secure fetch if you have it

    html = f"""<!doctype html>
<html>
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width,initial-scale=1"/>
  <title>Zoom SDK Join</title>
  <link rel="stylesheet" href="https://source.zoom.us/{ZOOM_SDK_VERSION}/css/bootstrap.css"/>
  <link rel="stylesheet" href="https://source.zoom.us/{ZOOM_SDK_VERSION}/css/react-select.css"/>
  <style>
    body {{ margin:0; font-family:system-ui, Arial; }}
    #zmmtg-root, #aria-notify-area {{ height: 100vh; }}
  </style>
</head>
<body>
  <div id="zmmtg-root"></div>
  <div id="aria-notify-area"></div>

  <script src="https://source.zoom.us/zoom-meeting-{ZOOM_SDK_VERSION}.min.js"></script>
  <script>
    const signature     = {json.dumps(signature)};
    const sdkKey        = {json.dumps(ZOOM_SDK_KEY)};
    const meetingNumber = {json.dumps(mn)};
    const passcode      = {json.dumps(pwd or "")};
    const userName      = {json.dumps(name)};
    const leaveUrl      = {json.dumps(leave)};
    const role          = {json.dumps(role)};
    const hostZAK       = {host_zak_js}; // required only if role === 1

    ZoomMtg.setZoomJSLib("https://source.zoom.us/{ZOOM_SDK_VERSION}/lib", "/av");
    ZoomMtg.preLoadWasm();
    ZoomMtg.prepareWebSDK();

    ZoomMtg.init({{
      leaveUrl,
      success: () => {{
        const joinCfg = {{
          signature,
          sdkKey,
          meetingNumber,
          userName,
          passWord: passcode
        }};
        if (role === 1 && typeof hostZAK === "string" && hostZAK.length > 0) {{
          joinCfg.zak = hostZAK;
        }}
        ZoomMtg.join({{
          ...joinCfg,
          success: () => console.log("Joined meeting"),
          error: (err) => console.error("Join error", err)
        }});
      }},
      error: (err) => console.error("Init error", err)
    }});
  </script>
</body>
</html>"""
    return HTMLResponse(content=html, status_code=200)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)