from supabase import create_client, Client
from datetime import datetime, timedelta
import os
from dotenv import load_dotenv
from typing import List, Dict, Optional
import logging

load_dotenv()

class SupabaseClient:
    def __init__(self):
        url = os.getenv("SUPABASE_URL")
        key = os.getenv("SUPABASE_KEY")
        if not url or key == "your_supabase_key":
            raise ValueError("Please set your SUPABASE_URL and SUPABASE_KEY in .env file")
        self.supabase: Client = create_client(url, key)
        self.logger = logging.getLogger(__name__)
        
        # Teacher ID for this agent
        self.teacher_id = "8fa571c3-676e-4121-beb4-be865610805f"

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
            ).eq(
                'school_id', school_id
            ).execute()
            
            if response.data:
                self.logger.info(f"Found {len(response.data)} courses for teacher")
                return response.data
            else:
                self.logger.warning(f"No courses found for teacher: {self.teacher_id} at school: {school_id}")
                return []
                
        except Exception as e:
            self.logger.error(f"Error fetching teacher courses: {e}")
            return []

    def get_course_lessons_with_pdfs(self, course_id: str) -> List[Dict]:
        """Get lessons with PDF resources for a specific course"""
        try:
            response = self.supabase.table('lessons').select('*').eq(
                'course_id', course_id
            ).execute()
            
            if not response.data:
                return []
            
            # Filter for PDF resources
            pdf_lessons = []
            for lesson in response.data:
                resources = lesson.get('resources')
                if self.is_pdf_resource(resources):
                    pdf_lessons.append(lesson)
            
            self.logger.info(f"Found {len(pdf_lessons)} PDF lessons for course: {course_id}")
            return pdf_lessons
            
        except Exception as e:
            self.logger.error(f"Error fetching course lessons: {e}")
            return []

    def get_all_teacher_lessons_with_courses(self) -> Dict:
        """Get all lessons organized by courses for the assigned teacher"""
        try:
            # Step 1: Get teacher's school_id
            school_id = self.get_teacher_school_id()
            if not school_id:
                return {'error': 'No school_id found for teacher'}
            
            # Step 2: Get teacher's courses
            courses = self.get_teacher_courses(school_id)
            if not courses:
                return {'error': 'No courses found for teacher'}
            
            # Step 3: Get lessons for each course
            teacher_data = {
                'teacher_id': self.teacher_id,
                'school_id': school_id,
                'courses': []
            }
            
            total_pdf_lessons = 0
            
            for course in courses:
                course_id = course['id']
                course_lessons = self.get_course_lessons_with_pdfs(course_id)
                
                course_data = {
                    'course_id': course_id,
                    'course_title': course['title'],
                    'course_description': course['description'],
                    'lessons': course_lessons,
                    'lesson_count': len(course_lessons)
                }
                
                teacher_data['courses'].append(course_data)
                total_pdf_lessons += len(course_lessons)
            
            teacher_data['total_courses'] = len(courses)
            teacher_data['total_pdf_lessons'] = total_pdf_lessons
            
            self.logger.info(f"Retrieved data for teacher: {total_pdf_lessons} PDF lessons across {len(courses)} courses")
            return teacher_data
            
        except Exception as e:
            self.logger.error(f"Error getting teacher lessons: {e}")
            return {'error': str(e)}

    def is_pdf_resource(self, resource: str) -> bool:
        """Check if resource is a direct PDF file"""
        if not resource or resource.strip().lower() in ['null', 'none', '', 'empty']:
            return False
        
        resource_str = str(resource).strip().lower()
        return (resource_str.startswith(('http://', 'https://')) and 
                resource_str.endswith('.pdf'))

    def get_lessons_with_pdf_resources(self, limit: int = 100) -> List[Dict]:
        """Get lessons with PDF resources for the assigned teacher (updated method)"""
        try:
            teacher_data = self.get_all_teacher_lessons_with_courses()
            
            if 'error' in teacher_data:
                self.logger.error(f"Error getting teacher lessons: {teacher_data['error']}")
                return []
            
            # Flatten all lessons from all courses
            all_lessons = []
            for course in teacher_data['courses']:
                for lesson in course['lessons']:
                    # Add course information to each lesson
                    lesson['course_title'] = course['course_title']
                    lesson['course_description'] = course['course_description']
                    lesson['course_id'] = course['course_id']
                    all_lessons.append(lesson)
            
            return all_lessons[:limit]
            
        except Exception as e:
            self.logger.error(f"Error fetching lessons: {e}")
            return []

    def get_teacher_info(self) -> Dict:
        """Get basic teacher information"""
        try:
            # First, let's try to get just the basic columns that should exist
            response = self.supabase.table('users').select('id, school_id').eq(
                'id', self.teacher_id
            ).execute()
            
            if response.data and len(response.data) > 0:
                user_data = response.data[0]
                # Add default values for missing columns
                user_data['name'] = user_data.get('name', 'Teacher')
                user_data['email'] = user_data.get('email', 'N/A')
                return user_data
            else:
                return {'error': 'Teacher not found'}
                
        except Exception as e:
            self.logger.error(f"Error fetching teacher info: {e}")
            # Try a more basic query to debug
            try:
                response = self.supabase.table('users').select('*').eq(
                    'id', self.teacher_id
                ).execute()
                
                if response.data and len(response.data) > 0:
                    user_data = response.data[0]
                    # Return whatever columns exist
                    return {
                        'id': user_data.get('id'),
                        'school_id': user_data.get('school_id'),
                        'name': user_data.get('name', user_data.get('full_name', user_data.get('username', 'Teacher'))),
                        'email': user_data.get('email', user_data.get('email_address', 'N/A'))
                    }
                else:
                    return {'error': 'Teacher not found'}
            except Exception as debug_error:
                return {'error': f'Database error: {str(debug_error)}'}

    def get_lesson_by_id(self, lesson_id: str) -> Optional[Dict]:
        """Get specific lesson by ID (unchanged)"""
        try:
            response = self.supabase.table('lessons').select('*').eq(
                'id', lesson_id
            ).execute()
            
            if response.data and len(response.data) > 0:
                return response.data[0]
            return None
        except Exception as e:
            self.logger.error(f"Error fetching lesson: {e}")
            return None

    def get_all_lessons_debug(self) -> List[Dict]:
        """Get all lessons for debugging purposes"""
        try:
            response = self.supabase.table('lessons').select('*').execute()
            return response.data if response.data else []
        except Exception as e:
            self.logger.error(f"Error fetching all lessons: {e}")
            return []

    def log_processing_result(self, lesson_id: str, success: bool, script_file: str = None, audio_file: str = None):
        """Log processing results"""
        try:
            self.logger.info(f"Lesson {lesson_id} processed. Success: {success}")
            if script_file:
                self.logger.info(f"Script saved: {script_file}")
            if audio_file:
                self.logger.info(f"Audio saved: {audio_file}")
        except Exception as e:
            self.logger.error(f"Error logging result: {e}")

    # Legacy method for backward compatibility
    def get_lessons_with_resources(self, limit: int = 100) -> List[Dict]:
        """Legacy method - now calls get_lessons_with_pdf_resources"""
        return self.get_lessons_with_pdf_resources(limit)