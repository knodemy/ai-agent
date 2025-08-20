# app.py
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from typing import Optional
from src.integrations.supabase_client import SupabaseClient  # import your updated client
import logging

app = FastAPI(title="Teacher Lessons API")

# Enable CORS (adjust origins as needed)
origins = [
    "http://localhost:3000",
    "http://127.0.0.1:3000",
    "*"  # allow all for testing; remove in production
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("api")

@app.get("/teacher/lessons")
async def get_teacher_lessons(teacher_id: str = Query(..., description="The ID of the teacher")):
    """
    Fetch all lessons organized by courses for a specific teacher.
    """
    try:
        print("hitting api from front end")
        client = SupabaseClient(teacher_id=teacher_id)
        data = client.get_all_teacher_lessons_with_courses()

        if 'error' in data:
            raise HTTPException(status_code=404, detail=data['error'])
        
        return data
    except Exception as e:
        logger.error(f"Error fetching teacher lessons: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/teacher/info")
async def get_teacher_info(teacher_id: str = Query(..., description="The ID of the teacher")):
    """
    Fetch basic teacher information
    """
    try:
        client = SupabaseClient(teacher_id=teacher_id)
        info = client.get_teacher_info()
        
        if 'error' in info:
            raise HTTPException(status_code=404, detail=info['error'])
        
        return info
    except Exception as e:
        logger.error(f"Error fetching teacher info: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/lesson/{lesson_id}")
async def get_lesson_by_id(lesson_id: str, teacher_id: str = Query(..., description="The ID of the teacher")):
    """
    Fetch a single lesson details by lesson ID for a teacher
    """
    try:
        client = SupabaseClient(teacher_id=teacher_id)
        lesson = client.get_lesson_by_id(lesson_id)
        
        if not lesson:
            raise HTTPException(status_code=404, detail="Lesson not found")
        
        return lesson
    except Exception as e:
        logger.error(f"Error fetching lesson {lesson_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))