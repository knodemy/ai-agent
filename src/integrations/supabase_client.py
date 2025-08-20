# supabase_client.py
from supabase import create_client, Client
from datetime import datetime, timedelta
import os
from dotenv import load_dotenv
from typing import List, Dict, Optional
import logging

load_dotenv()

class SupabaseClient:
    def __init__(self, teacher_id: str):
        """
        Initialize Supabase client and assign a teacher_id
        """
        url = os.getenv("SUPABASE_URL")
        key = os.getenv("SUPABASE_KEY")
        if not url or not key or key == "your_supabase_key":
            raise ValueError("Please set your SUPABASE_URL and SUPABASE_KEY in .env file")

        self.supabase: Client = create_client(url, key)
        self.logger = logging.getLogger(__name__)
        logging.basicConfig(level=logging.INFO)
        self.teacher_id = teacher_id  # Teacher ID for this instance

    def get_teacher_school_id(self) -> Optional[str]:
        """Get school_id for the assigned teacher"""
        try:
            response = self.supabase.table('users').select('school_id').eq(
                'id', self.teacher_id
            ).execute()
            
            if response.data and len(response.data) > 0:
                school_id = response.data[0]['school_id']
                self.logger.info(f"Found school_id: {school_id} for teacher: {self.teacher_id}")
                return school_id
            else:
                self.logger.warning(f"No school_id found for teacher: {self.teacher_id}")
                return None
        except Exception as e:
            self.logger.error(f"Error fetching teacher school_id: {e}")
            return None

    def get_teacher_courses(self, school_id: str) -> List[Dict]:
        """Get all courses for the teacher at their school"""
        try:
            response = self.supabase.table('courses').select('id, title, description').eq(
                'teacher_id', self.teacher_id
            ).eq('school_id', school_id).execute()
            
            if response.data:
                self.logger.info(f"Found {len(response.data)} courses for teacher")
                return response.data
            else:
                self.logger.warning(f"No courses found for teacher: {self.teacher_id} at school: {school_id}")
                return []
        except Exception as e:
            self.logger.error(f"Error fetching teacher courses: {e}")
            return []

    def get_course_lessons(self, course_id: str) -> List[Dict]:
        """Get lessons for a specific course"""
        try:
            response = self.supabase.table('lessons').select('*').eq(
                'course_id', course_id
            ).order('created_at', {'ascending': False}).execute()  # descending order
            return response.data if response.data else []
        except Exception as e:
            self.logger.error(f"Error fetching lessons for course {course_id}: {e}")
            return []

    def get_all_teacher_lessons_with_courses(self) -> Dict:
        """Get all lessons organized by courses for the assigned teacher"""
        try:
            school_id = self.get_teacher_school_id()
            if not school_id:
                return {'error': 'No school_id found for teacher'}
            
            courses = self.get_teacher_courses(school_id)
            if not courses:
                return {'error': 'No courses found for teacher'}
            
            teacher_data = {
                'teacher_id': self.teacher_id,
                'school_id': school_id,
                'courses': []
            }
            
            for course in courses:
                course_id = course['id']
                lessons = self.get_course_lessons(course_id)
                
                teacher_data['courses'].append({
                    'course_id': course_id,
                    'course_title': course['title'],
                    'course_description': course['description'],
                    'lessons': lessons,
                    'lesson_count': len(lessons)
                })
            
            teacher_data['total_courses'] = len(courses)
            teacher_data['total_lessons'] = sum(len(c['lessons']) for c in teacher_data['courses'])
            
            return teacher_data
        except Exception as e:
            self.logger.error(f"Error getting teacher lessons: {e}")
            return {'error': str(e)}

    def get_teacher_info(self) -> Dict:
        """Get basic teacher information, construct 'name' from first_name + last_name"""
        try:
            response = self.supabase.table('users').select('id, school_id, first_name, last_name, email').eq(
                'id', self.teacher_id
            ).execute()
            
            if response.data and len(response.data) > 0:
                user_data = response.data[0]
                user_data['name'] = f"{user_data.get('first_name', '')} {user_data.get('last_name', '')}".strip()
                self.logger.info(f"Fetched teacher info for {self.teacher_id}: {user_data}")
                return user_data
            else:
                self.logger.warning(f"Teacher not found: {self.teacher_id}")
                return {'error': 'Teacher not found'}
        except Exception as e:
            self.logger.error(f"Error fetching teacher info: {e}")
            return {'error': str(e)}
