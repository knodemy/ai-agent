# app.py
import os
import io
import json
import logging
from datetime import datetime, timedelta
from typing import Optional, List

from fastapi import FastAPI, HTTPException, UploadFile, File, Form, Body, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi import BackgroundTasks
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

# UPDATED: Import enhanced speech generator
try:
    from src.core.speech_generator import EnhancedTimedSpeechGenerator
    # Create aliases for compatibility
    SpeechGenerator = EnhancedTimedSpeechGenerator
    TimedSpeechGenerator = EnhancedTimedSpeechGenerator
    generate_audio_for_prepared_lessons = None  # Not implemented yet
    generate_timed_audio_for_prepared_lessons = None  # Not implemented yet
except Exception:
    try:
        from speech_generator import EnhancedTimedSpeechGenerator
        # Create aliases for compatibility
        SpeechGenerator = EnhancedTimedSpeechGenerator
        TimedSpeechGenerator = EnhancedTimedSpeechGenerator
        generate_audio_for_prepared_lessons = None
        generate_timed_audio_for_prepared_lessons = None
    except Exception as e:
        print(f"Failed to import EnhancedTimedSpeechGenerator: {e}")
        SpeechGenerator = None
        TimedSpeechGenerator = None
        generate_audio_for_prepared_lessons = None
        generate_timed_audio_for_prepared_lessons = None

logger = logging.getLogger("api")
logging.basicConfig(level=logging.INFO)

app = FastAPI(title="Teacher Lessons API")

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
GENERATE_TIMED_AUDIO = os.getenv("GENERATE_TIMED_AUDIO", "true").lower() == "true"  # NEW

# Initialize scheduler
scheduler = AsyncIOScheduler()

# Models
class ScriptPayload(BaseModel):
    teacher_id: str = Field(..., description="UUID of teacher")
    course_id: str = Field(..., description="UUID of course")
    course_title: Optional[str] = None
    lesson_id: str = Field(..., description="UUID of lesson")
    lesson_title: Optional[str] = None
    audience: Optional[str] = "middle school (ages 11-14)"
    language: Optional[str] = "English"
    script_text: str = Field(..., description="Raw lesson script text")
    meta: Optional[dict] = None

class UploadResponse(BaseModel):
    teacher_id: str
    course_id: str
    lesson_id: str
    bucket_path: str
    public_url: Optional[str] = None
    signed_url: Optional[str] = None
    recorded_id: Optional[str] = None

class DailyGenerationResult(BaseModel):
    date: str
    total_courses: int
    total_lessons_processed: int
    successful_generations: int
    failed_generations: int
    successful_audio_generations: int = 0
    failed_audio_generations: int = 0
    errors: List[dict] = []

class AudioGenerationResult(BaseModel):
    teacher_id: str
    course_id: str
    date: str
    total_lessons: int
    successful_audio_generations: int
    failed_audio_generations: int
    errors: List[dict] = []

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

async def get_all_courses_for_tomorrow() -> List[dict]:
    """Get all courses that have next_session = tomorrow"""
    tomorrow = get_tomorrow_date()
    
    try:
        from supabase import create_client
        
        url = os.getenv("SUPABASE_URL")
        key = os.getenv("SUPABASE_KEY")
        supabase = create_client(url, key)
        
        # Get courses where next_session = tomorrow
        response = supabase.table('courses').select(
            'id, title, teacher_id, nextsession'
        ).eq('nextsession', tomorrow).execute()
        
        courses = response.data or []
        logger.info(f"Found {len(courses)} courses for tomorrow ({tomorrow})")
        return courses
        
    except Exception as e:
        logger.error(f"Error fetching tomorrow's courses: {e}")
        return []

async def get_courses_for_teacher_tomorrow(teacher_id: str) -> List[dict]:
    """Get courses for a specific teacher that have next_session = tomorrow"""
    tomorrow = get_tomorrow_date()
    
    try:
        from supabase import create_client
        
        url = os.getenv("SUPABASE_URL")
        key = os.getenv("SUPABASE_KEY")
        supabase = create_client(url, key)
        
        # Get courses where next_session = tomorrow AND teacher_id = specific teacher
        response = supabase.table('courses').select(
            'id, title, teacher_id, nextsession'
        ).eq('nextsession', tomorrow).eq('teacher_id', teacher_id).execute()
        
        courses = response.data or []
        logger.info(f"Found {len(courses)} courses for teacher {teacher_id} tomorrow ({tomorrow})")
        return courses
        
    except Exception as e:
        logger.error(f"Error fetching tomorrow's courses for teacher {teacher_id}: {e}")
        return []

async def process_course_for_scripts(course: dict) -> dict:
    """Process a single course to generate scripts for lessons with PDFs"""
    course_id = course['id']
    teacher_id = course['teacher_id']
    course_title = course['title']
    date = get_tomorrow_date()
    
    result = {
        'course_id': course_id,
        'teacher_id': teacher_id,
        'course_title': course_title,
        'lessons_processed': 0,
        'successful_generations': 0,
        'failed_generations': 0,
        'successful_audio_generations': 0,
        'failed_audio_generations': 0,
        'errors': []
    }
    
    try:
        client = SupabaseClient(teacher_id=teacher_id)
        
        # Get lessons with PDF resources for this course
        lessons_with_pdfs = client.get_lessons_with_pdf_resources(course_id)
        result['lessons_processed'] = len(lessons_with_pdfs)
        
        if not lessons_with_pdfs:
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
                        # Generate script PDF
                        script_pack = cp.generate_script_pdf_bytes(
                            pdf_source_url=pdf_url,
                            lesson_title=lesson_title,
                            teacher_name=teacher_name,
                            audience="middle school (ages 11-14)",
                            language="English"
                        )
                        
                        # Build bucket path with date
                        bucket_path = _build_bucket_path(
                            teacher_id, course_id, lesson_id, date, ext="pdf"
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
                        logger.info(f"Generated script for lesson {lesson_id}, PDF {idx}")
                        
                        # DEBUG: Add debug logging
                        logger.info(f"DEBUG: GENERATE_TIMED_AUDIO = {GENERATE_TIMED_AUDIO}")
                        logger.info(f"DEBUG: TimedSpeechGenerator available = {TimedSpeechGenerator is not None}")
                        logger.info(f"DEBUG: SpeechGenerator available = {SpeechGenerator is not None}")
                        logger.info(f"DEBUG: file_url = {file_url}")
                        
                        # UPDATED: Generate timed audio after script is created
                        if file_url:
                            logger.info(f"DEBUG: Entering audio generation for lesson {lesson_id}")
                            try:
                                if GENERATE_TIMED_AUDIO and TimedSpeechGenerator:
                                    logger.info("DEBUG: Attempting timed audio generation...")
                                    # Generate timed audio segments using EnhancedTimedSpeechGenerator
                                    timed_speech_gen = TimedSpeechGenerator()
                                    logger.info("DEBUG: EnhancedTimedSpeechGenerator instance created")
                                    
                                    audio_result = timed_speech_gen.generate_timed_lesson_audio(
                                        teacher_id=teacher_id,
                                        course_id=course_id,
                                        lesson_id=lesson_id,
                                        lesson_title=lesson_title,
                                        script_url=file_url,
                                        date=date,
                                        voice="alloy"
                                    )
                                    
                                    logger.info(f"DEBUG: Audio result = {audio_result}")
                                    
                                    if audio_result['success']:
                                        result['successful_audio_generations'] += 1
                                        logger.info(f"Generated combined audio for lesson {lesson_id} ({audio_result.get('duration_minutes', 0)} min)")
                                    else:
                                        result['failed_audio_generations'] += 1
                                        result['errors'].append({
                                            'lesson_id': lesson_id,
                                            'type': 'timed_audio_generation',
                                            'error': audio_result.get('error', 'Unknown audio error')
                                        })
                                        logger.error(f"Failed to generate timed audio for lesson {lesson_id}: {audio_result.get('error')}")
                                
                                else:
                                    logger.error("DEBUG: No timed audio generators available!")
                                    result['failed_audio_generations'] += 1
                                    result['errors'].append({
                                        'lesson_id': lesson_id,
                                        'type': 'audio_generation',
                                        'error': 'No audio generators available'
                                    })
                                        
                            except Exception as audio_error:
                                result['failed_audio_generations'] += 1
                                result['errors'].append({
                                    'lesson_id': lesson_id,
                                    'type': 'audio_generation',
                                    'error': str(audio_error)
                                })
                                logger.error(f"Audio generation exception for lesson {lesson_id}: {audio_error}")
                                import traceback
                                logger.error(f"Audio generation traceback: {traceback.format_exc()}")
                        else:
                            logger.error(f"DEBUG: No file_url for lesson {lesson_id}")
                            
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

# Scheduled job function
async def generate_daily_scripts():
    """Main function that runs daily to generate scripts and audio"""
    start_time = datetime.now()
    logger.info(f"Starting daily script and audio generation at {start_time}")
    
    try:
        # Get courses for tomorrow (all teachers)
        courses = await get_all_courses_for_tomorrow()
        
        if not courses:
            logger.info("No courses found for tomorrow")
            return
        
        # Process each course
        total_successful = 0
        total_failed = 0
        total_lessons = 0
        total_successful_audio = 0
        total_failed_audio = 0
        all_errors = []
        
        for course in courses:
            result = await process_course_for_scripts(course)
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
            'date': get_tomorrow_date(),
            'total_courses': len(courses),
            'total_lessons_processed': total_lessons,
            'successful_generations': total_successful,
            'failed_generations': total_failed,
            'successful_audio_generations': total_successful_audio,
            'failed_audio_generations': total_failed_audio,
            'duration_seconds': duration,
            'errors': all_errors
        }
        
        logger.info(f"Daily script and audio generation completed: {summary}")
        
    except Exception as e:
        logger.error(f"Daily script and audio generation failed: {e}")

# Schedule the daily job
@scheduler.scheduled_job('cron', hour=10, minute=16, timezone='UTC')
async def scheduled_daily_script_generation():
    """Scheduled job that runs daily"""
    await generate_daily_scripts()

# API Endpoints

@app.post("/scripts/generate-for-teacher")
async def generate_scripts_for_specific_teacher(teacher_id: str = Query(...)):
    """Generate scripts and audio for a specific teacher's tomorrow courses"""
    try:
        courses = await get_courses_for_teacher_tomorrow(teacher_id)
        
        if not courses:
            return {"message": f"No courses found for teacher {teacher_id} tomorrow"}
        
        # Process courses for this specific teacher
        total_successful = 0
        total_failed = 0
        total_lessons = 0
        total_successful_audio = 0
        total_failed_audio = 0
        all_errors = []
        
        for course in courses:
            result = await process_course_for_scripts(course)
            total_lessons += result['lessons_processed']
            total_successful += result['successful_generations']
            total_failed += result['failed_generations']
            total_successful_audio += result['successful_audio_generations']
            total_failed_audio += result['failed_audio_generations']
            all_errors.extend(result['errors'])
        
        return {
            "teacher_id": teacher_id,
            "tomorrow_date": get_tomorrow_date(),
            "courses_processed": len(courses),
            "lessons_processed": total_lessons,
            "successful_generations": total_successful,
            "failed_generations": total_failed,
            "successful_audio_generations": total_successful_audio,
            "failed_audio_generations": total_failed_audio,
            "errors": all_errors
        }
        
    except Exception as e:
        logger.error(f"Teacher-specific generation failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/scripts/generate-daily", response_model=DailyGenerationResult)
async def trigger_daily_generation():
    """Manually trigger daily script and audio generation for testing"""
    try:
        courses = await get_all_courses_for_tomorrow()
        
        if not courses:
            return DailyGenerationResult(
                date=get_tomorrow_date(),
                total_courses=0,
                total_lessons_processed=0,
                successful_generations=0,
                failed_generations=0,
                successful_audio_generations=0,
                failed_audio_generations=0,
                errors=[]
            )
        
        total_successful = 0
        total_failed = 0
        total_lessons = 0
        total_successful_audio = 0
        total_failed_audio = 0
        all_errors = []
        
        for course in courses:
            result = await process_course_for_scripts(course)
            total_lessons += result['lessons_processed']
            total_successful += result['successful_generations']
            total_failed += result['failed_generations']
            total_successful_audio += result['successful_audio_generations']
            total_failed_audio += result['failed_audio_generations']
            all_errors.extend(result['errors'])
        
        return DailyGenerationResult(
            date=get_tomorrow_date(),
            total_courses=len(courses),
            total_lessons_processed=total_lessons,
            successful_generations=total_successful,
            failed_generations=total_failed,
            successful_audio_generations=total_successful_audio,
            failed_audio_generations=total_failed_audio,
            errors=all_errors
        )
        
    except Exception as e:
        logger.error(f"Manual daily generation failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# NEW: Timed audio specific endpoints
@app.post("/audio/generate-timed-segments")
async def generate_timed_audio_segments(
    teacher_id: str = Query(...),
    course_id: str = Query(...),
    lesson_id: str = Query(...),
    script_url: str = Query(...),
    lesson_title: str = Query("Lesson"),
    voice: str = Query("alloy"),
    date: str = Query(None)
):
    """Generate timed audio segments for a single lesson"""
    try:
        if not TimedSpeechGenerator:
            raise HTTPException(status_code=500, detail="Timed speech generator not available")
        
        target_date = date or get_tomorrow_date()
        
        timed_speech_gen = TimedSpeechGenerator()  # This is now EnhancedTimedSpeechGenerator
        result = timed_speech_gen.generate_timed_lesson_audio(
            teacher_id=teacher_id,
            course_id=course_id,
            lesson_id=lesson_id,
            lesson_title=lesson_title,
            script_url=script_url,
            date=target_date,
            voice=voice
        )
        
        return result
        
    except Exception as e:
        logger.error(f"Timed audio generation failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/audio/playlist/{teacher_id}/{course_id}/{lesson_id}")
async def get_lesson_playlist(
    teacher_id: str,
    course_id: str, 
    lesson_id: str,
    date: str = Query(None)
):
    """Get the playlist data for a lesson's audio segments"""
    try:
        target_date = date or get_tomorrow_date()
        
        client = SupabaseClient(teacher_id=teacher_id)
        
        # Get playlist URL from storage
        playlist_path = f"{teacher_id}/{course_id}/{target_date}/segments/{lesson_id}_playlist.json"
        
        # Try to get the playlist file
        if SIGN_URLS:
            playlist_url = client.create_signed_url(
                AUDIO_BUCKET, playlist_path, expires_in=3600
            )
        else:
            playlist_url = client.get_public_url(AUDIO_BUCKET, playlist_path)
        
        if playlist_url:
            # Fetch the playlist content
            import requests
            response = requests.get(playlist_url)
            if response.status_code == 200:
                return response.json()
        
        return {"error": "Playlist not found"}
        
    except Exception as e:
        logger.error(f"Error fetching playlist: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/audio/generate-timed-for-course")
async def generate_timed_audio_for_course(
    teacher_id: str = Query(...),
    course_id: str = Query(...),
    date: str = Query(None),
    voice: str = Query("alloy")
):
    """Generate timed audio segments for all prepared lessons in a course"""
    try:
        if not generate_timed_audio_for_prepared_lessons:
            raise HTTPException(status_code=500, detail="Timed audio generation not available")
        
        target_date = date or get_tomorrow_date()
        
        result = generate_timed_audio_for_prepared_lessons(
            teacher_id=teacher_id,
            course_id=course_id,
            date=target_date,
            voice=voice
        )
        
        return result
        
    except Exception as e:
        logger.error(f"Course timed audio generation failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# Regular audio endpoints
@app.post("/audio/generate-for-course")
async def generate_audio_for_course(
    teacher_id: str = Query(...),
    course_id: str = Query(...),
    date: str = Query(None),
    voice: str = Query("alloy")
):
    """Generate regular audio for all prepared lessons in a specific course"""
    try:
        if not generate_audio_for_prepared_lessons:
            raise HTTPException(status_code=500, detail="Speech generator not available")
        
        target_date = date or get_tomorrow_date()
        
        result = generate_audio_for_prepared_lessons(
            teacher_id=teacher_id,
            course_id=course_id,
            date=target_date,
            voice=voice
        )
        
        return result
        
    except Exception as e:
        logger.error(f"Course audio generation failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/audio/generate-single-lesson")
async def generate_audio_for_single_lesson(
    teacher_id: str = Query(...),
    course_id: str = Query(...),
    lesson_id: str = Query(...),
    script_url: str = Query(...),
    lesson_title: str = Query("Lesson"),
    voice: str = Query("alloy"),
    date: str = Query(None)
):
    """Generate regular audio for a single lesson"""
    try:
        if not SpeechGenerator:
            raise HTTPException(status_code=500, detail="Speech generator not available")
        
        target_date = date or get_tomorrow_date()
        
        speech_gen = SpeechGenerator()
        result = speech_gen.generate_lesson_audio(
            teacher_id=teacher_id,
            course_id=course_id,
            lesson_id=lesson_id,
            lesson_title=lesson_title,
            script_url=script_url,
            date=target_date,
            voice=voice
        )
        
        return result
        
    except Exception as e:
        logger.error(f"Single lesson audio generation failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/audio/voices")
async def get_available_voices():
    """Get list of available voices for text-to-speech"""
    return {
        "voices": ["alloy", "echo", "fable", "onyx", "nova", "shimmer"],
        "default": "alloy"
    }

@app.get("/scripts/preview-tomorrow")
async def preview_tomorrow_courses():
    """Preview what courses will be processed tomorrow"""
    try:
        courses = await get_all_courses_for_tomorrow()
        return {
            'tomorrow_date': get_tomorrow_date(),
            'courses_count': len(courses),
            'courses': courses
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# Debug endpoints for troubleshooting
@app.get("/debug/scheduler-status")
async def get_scheduler_status():
    """Debug endpoint to check scheduler status and next run time"""
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
        "tomorrow_date": get_tomorrow_date(),
        "timed_audio_enabled": GENERATE_TIMED_AUDIO,
        "components_available": {
            "EnhancedTimedSpeechGenerator": TimedSpeechGenerator is not None,
            "ContentProcessor": ContentProcessor is not None
        }
    }

# Your existing endpoints
@app.get("/teacher/info")
def get_teacher_info(teacher_id: str = Query(...)):
    try:
        client = SupabaseClient(teacher_id=teacher_id)
        info = client.get_teacher_info()
        
        if 'error' in info:
            raise HTTPException(status_code=404, detail=info['error'])
        
        return info
    except Exception as e:
        logger.error(f"Error fetching teacher info: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/teacher/lessons")
def get_teacher_lessons(teacher_id: str = Query(...)):
    try:
        client = SupabaseClient(teacher_id=teacher_id)
        data = client.get_all_teacher_lessons_with_courses()
        
        if 'error' in data:
            raise HTTPException(status_code=404, detail=data['error'])
        
        return data
    except Exception as e:
        logger.error(f"Error fetching teacher lessons: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# App lifecycle events
@app.on_event("startup")
async def start_scheduler():
    """Start the scheduler when the app starts"""
    scheduler.start()
    logger.info("APScheduler started successfully")
    logger.info(f"Timed audio generation: {'enabled' if GENERATE_TIMED_AUDIO else 'disabled'}")

@app.on_event("shutdown")
async def shutdown_scheduler():
    """Gracefully shutdown the scheduler when the app stops"""
    scheduler.shutdown()
    logger.info("APScheduler shutdown complete")

@app.post("/zoom/schedule-sessions")
async def schedule_zoom_sessions(teacher_id: str = Query(...)):
    """Schedule Zoom sessions for a teacher's courses today"""
    try:
        from integrated_zoom_teacher import IntegratedZoomTeacher
        
        # Create the agent
        agent = IntegratedZoomTeacher()
        
        # Get scheduled courses for the teacher
        courses = agent.get_scheduled_courses(teacher_id)
        
        if not courses:
            return {"message": f"No courses scheduled for teacher {teacher_id} today"}
        
        # Format response
        scheduled_courses = []
        for course in courses:
            scheduled_courses.append({
                "course_id": course.course_id,
                "course_title": course.course_title, 
                "start_time": course.start_time.isoformat(),
                "zoom_link": course.zoom_link,
                "has_audio": course.lesson_audio_url is not None
            })
        
        return {
            "teacher_id": teacher_id,
            "scheduled_courses_today": len(courses),
            "courses": scheduled_courses
        }
        
    except Exception as e:
        logger.error(f"Failed to get scheduled sessions: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/zoom/start-agent")
async def start_zoom_agent(
    background_tasks: BackgroundTasks,
    teacher_id: str = Query(...)
):
    """Start the AI Teacher Zoom agent in the background for a specific teacher"""
    try:
        from integrated_zoom_teacher import IntegratedZoomTeacher
        
        def run_agent():
            agent = IntegratedZoomTeacher()
            agent.schedule_and_run_sessions(teacher_id)
        
        # Start the agent in the background
        background_tasks.add_task(run_agent)
        
        return {
            "message": "AI Teacher Zoom agent started",
            "teacher_id": teacher_id,
            "status": "running"
        }
        
    except Exception as e:
        logger.error(f"Failed to start Zoom agent: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/courses/today")
async def get_courses_today(teacher_id: str = Query(...)):
    """Get courses scheduled for today for a specific teacher with lesson details"""
    try:
        from datetime import date
        today = date.today()
        
        url = os.getenv("SUPABASE_URL")
        key = os.getenv("SUPABASE_KEY")
        supabase = create_client(url, key)
        
        # Get courses for specific teacher - don't filter by date since start_time is TIME not TIMESTAMP
        response = supabase.table('courses').select(
            'id, title, teacher_id, start_time, end_time, zoomLink'
        ).eq('teacher_id', teacher_id).execute()
        
        courses_today = []
        for course in response.data:
            # Check if there's audio available for this specific course
            # First get prepared lessons for this teacher
            prepared_lessons_response = supabase.table('prepared_lessons').select(
                'lesson_id, audio_url, url'
            ).eq('teacher_id', teacher_id).not_.is_('audio_url', 'null').execute()
            
            lesson_with_audio = None
            has_audio = False
            
            if prepared_lessons_response.data:
                # Check if any of these lessons belong to this course
                for prep_lesson in prepared_lessons_response.data:
                    lesson_check = supabase.table('lessons').select(
                        'id, title, course_id'
                    ).eq('id', prep_lesson['lesson_id']).eq('course_id', course['id']).execute()
                    
                    if lesson_check.data:
                        has_audio = True
                        lesson_with_audio = {
                            "lesson_id": prep_lesson['lesson_id'],
                            "lesson_title": lesson_check.data[0]['title'],
                            "audio_url": prep_lesson['audio_url'],
                            "script_url": prep_lesson['url']
                        }
                        break
            
            courses_today.append({
                "course_id": course['id'],
                "course_title": course['title'],
                "teacher_id": course['teacher_id'],
                "start_time": course['start_time'],
                "end_time": course['end_time'],
                "zoom_link": course.get('zoomLink'),
                "has_audio": has_audio,
                "lesson_details": lesson_with_audio
            })
        
        return {
            "teacher_id": teacher_id,
            "date": today.isoformat(),
            "courses_count": len(courses_today),
            "courses": courses_today
        }
        
    except Exception as e:
        logger.error(f"Failed to get today's courses: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/courses/update-zoom-link")
async def update_course_zoom_link(
    teacher_id: str = Query(...),
    course_id: str = Query(...),
    zoom_link: str = Query(...)
):
    """Update the Zoom link for a course"""
    try:
        url = os.getenv("SUPABASE_URL")
        key = os.getenv("SUPABASE_KEY")
        supabase = create_client(url, key)
        
        # Update the course with the Zoom link (verify teacher ownership)
        response = supabase.table('courses').update({
            'zoomLink': zoom_link
        }).eq('id', course_id).eq('teacher_id', teacher_id).execute()
        
        if not response.data:
            raise HTTPException(status_code=404, detail="Course not found or not owned by teacher")
        
        return {
            "teacher_id": teacher_id,
            "course_id": course_id,
            "zoom_link": zoom_link,
            "updated": True
        }
        
    except Exception as e:
        logger.error(f"Failed to update Zoom link: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# Add debug endpoint
@app.get("/debug/course-lessons")
async def debug_course_lessons(teacher_id: str = Query(...), course_id: str = Query(None)):
    """Debug endpoint to check course-lesson relationships and audio availability"""
    try:
        url = os.getenv("SUPABASE_URL")
        key = os.getenv("SUPABASE_KEY")
        supabase = create_client(url, key)
        
        debug_info = {
            "teacher_id": teacher_id,
            "course_id": course_id,
            "prepared_lessons": [],
            "course_lessons": [],
            "matches": []
        }
        
        # Get all prepared lessons for this teacher
        prepared_response = supabase.table('prepared_lessons').select(
            'lesson_id, audio_url, url, created_at'
        ).eq('teacher_id', teacher_id).execute()
        
        debug_info["prepared_lessons"] = prepared_response.data
        
        # Get lessons for the specific course (if provided)
        if course_id:
            lessons_response = supabase.table('lessons').select(
                'id, title, course_id, created_at'
            ).eq('course_id', course_id).execute()
            
            debug_info["course_lessons"] = lessons_response.data
            
            # Find matches
            prepared_lesson_ids = [p['lesson_id'] for p in prepared_response.data]
            course_lesson_ids = [l['id'] for l in lessons_response.data]
            
            matches = []
            for lesson in lessons_response.data:
                if lesson['id'] in prepared_lesson_ids:
                    # Find the prepared lesson details
                    prep_lesson = next((p for p in prepared_response.data if p['lesson_id'] == lesson['id']), None)
                    matches.append({
                        "lesson_id": lesson['id'],
                        "lesson_title": lesson['title'],
                        "has_script": prep_lesson['url'] is not None if prep_lesson else False,
                        "has_audio": prep_lesson['audio_url'] is not None if prep_lesson else False,
                        "audio_url": prep_lesson['audio_url'] if prep_lesson else None
                    })
            
            debug_info["matches"] = matches
        
        return debug_info
        
    except Exception as e:
        logger.error(f"Debug endpoint failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)