# app.py
import os
import io
import json
import logging
from datetime import datetime, timedelta
from typing import Optional, List

from fastapi import FastAPI, HTTPException, UploadFile, File, Form, Body, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

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

logger = logging.getLogger("api")
logging.basicConfig(level=logging.INFO)

app = FastAPI(title="Teacher Lessons API")

# CORS configuration
origins = [
   "http://localhost:8080",
    "http://localhost:3000", 
    "http://127.0.0.1:8080",
    "http://127.0.0.1:3000",
    "https://teacher.knodemy.ai/",
    "https://devteacher.knodemy.ai/"
    "http://18.224.67.22:8000"
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Settings
SCRIPTS_BUCKET = os.getenv("SCRIPTS_BUCKET", "lecture-scripts")
SIGN_URLS = os.getenv("SIGN_URLS", "true").lower() == "true"
SIGN_EXPIRES_SECONDS = int(os.getenv("SIGN_EXPIRES_SECONDS", "3600"))

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
    errors: List[dict] = []

# Helper functions
def get_tomorrow_date() -> str:
    """Get tomorrow's date in YYYY-MM-DD format"""
    tomorrow = datetime.now() + timedelta(days=1)
    return tomorrow.strftime('%Y-%m-%d')

def get_today_date() -> str:
    """Get today's date in YYYY-MM-DD format"""
    return datetime.now().strftime('%Y-%m-%d')

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

async def get_courses_for_tomorrow() -> List[dict]:
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
                        
                    except Exception as pdf_error:
                        result['failed_generations'] += 1
                        error_info = {
                            'lesson_id': lesson_id,
                            'pdf_url': pdf_url,
                            'error': str(pdf_error)
                        }
                        result['errors'].append(error_info)
                        logger.error(f"Failed to process PDF {pdf_url}: {pdf_error}")
                        
            except Exception as lesson_error:
                result['failed_generations'] += 1
                error_info = {
                    'lesson_id': lesson_id,
                    'error': str(lesson_error)
                }
                result['errors'].append(error_info)
                logger.error(f"Failed to process lesson {lesson_id}: {lesson_error}")
        
        return result
        
    except Exception as e:
        result['failed_generations'] = result['lessons_processed']
        result['errors'].append({
            'course_id': course_id,
            'error': str(e)
        })
        logger.error(f"Failed to process course {course_id}: {e}")
        return result

# Scheduled job function
async def generate_daily_scripts():
    """Main function that runs daily at 12:00 AM to generate scripts"""
    start_time = datetime.now()
    logger.info(f"Starting daily script generation at {start_time}")
    
    try:
        # Get courses for tomorrow (all teachers)
        courses = await get_all_courses_for_tomorrow()
        
        # Get courses for tomorrow (all teachers)
        courses = await get_all_courses_for_tomorrow()
        
        if not courses:
            logger.info("No courses found for tomorrow")
            return
        
        # Process each course
        total_successful = 0
        total_failed = 0
        total_lessons = 0
        all_errors = []
        
        for course in courses:
            result = await process_course_for_scripts(course)
            total_lessons += result['lessons_processed']
            total_successful += result['successful_generations']
            total_failed += result['failed_generations']
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
            'duration_seconds': duration,
            'errors': all_errors
        }
        
        logger.info(f"Daily script generation completed: {summary}")
        
        # Optionally, save this summary to a database table for monitoring
        
    except Exception as e:
        logger.error(f"Daily script generation failed: {e}")

# Schedule the daily job
@scheduler.scheduled_job('cron', hour=7, minute=44, timezone='UTC')
async def scheduled_daily_script_generation():
    """Scheduled job that runs at 12:00 AM UTC daily"""
    await generate_daily_scripts()

# API Endpoints

@app.post("/scripts/generate-for-teacher")
async def generate_scripts_for_specific_teacher(teacher_id: str = Query(...)):
    """Generate scripts for a specific teacher's tomorrow courses"""
    try:
        courses = await get_courses_for_teacher_tomorrow(teacher_id)
        
        if not courses:
            return {"message": f"No courses found for teacher {teacher_id} tomorrow"}
        
        # Process courses for this specific teacher
        total_successful = 0
        total_failed = 0
        total_lessons = 0
        all_errors = []
        
        for course in courses:
            result = await process_course_for_scripts(course)
            total_lessons += result['lessons_processed']
            total_successful += result['successful_generations']
            total_failed += result['failed_generations']
            all_errors.extend(result['errors'])
        
        return {
            "teacher_id": teacher_id,
            "tomorrow_date": get_tomorrow_date(),
            "courses_processed": len(courses),
            "lessons_processed": total_lessons,
            "successful_generations": total_successful,
            "failed_generations": total_failed,
            "errors": all_errors
        }
        
    except Exception as e:
        logger.error(f"Teacher-specific generation failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/scripts/generate-daily", response_model=DailyGenerationResult)
async def trigger_daily_generation():
    """Manually trigger daily script generation for testing"""
    try:
        courses = await get_courses_for_tomorrow()
        
        if not courses:
            return DailyGenerationResult(
                date=get_tomorrow_date(),
                total_courses=0,
                total_lessons_processed=0,
                successful_generations=0,
                failed_generations=0,
                errors=[]
            )
        
        total_successful = 0
        total_failed = 0
        total_lessons = 0
        all_errors = []
        
        for course in courses:
            result = await process_course_for_scripts(course)
            total_lessons += result['lessons_processed']
            total_successful += result['successful_generations']
            total_failed += result['failed_generations']
            all_errors.extend(result['errors'])
        
        return DailyGenerationResult(
            date=get_tomorrow_date(),
            total_courses=len(courses),
            total_lessons_processed=total_lessons,
            successful_generations=total_successful,
            failed_generations=total_failed,
            errors=all_errors
        )
        
    except Exception as e:
        logger.error(f"Manual daily generation failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/scripts/preview-tomorrow")
async def preview_tomorrow_courses():
    """Preview what courses will be processed tomorrow"""
    try:
        courses = await get_courses_for_tomorrow()
        return {
            'tomorrow_date': get_tomorrow_date(),
            'courses_count': len(courses),
            'courses': courses
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

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

@app.on_event("shutdown")
async def shutdown_scheduler():
    """Gracefully shutdown the scheduler when the app stops"""
    scheduler.shutdown()
    logger.info("APScheduler shutdown complete")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)