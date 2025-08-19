from supabase import create_client, Client
from datetime import datetime, timedelta
import os
from dotenv import load_dotenv
from typing import List, Dict, Optional
import logging

load_dotenv()

class SupabaseClient:
    def __init__(self, teacher_id: Optional[str] = None):
        url = os.getenv("SUPABASE_URL")
        key = os.getenv("SUPABASE_KEY")
        if not url or key == "your_supabase_key":
            raise ValueError("Please set your SUPABASE_URL and SUPABASE_KEY in .env file")
        self.supabase: Client = create_client(url, key)
        self.logger = logging.getLogger(__name__)
        
        # Teacher ID is now optional and dynamic
        self.teacher_id = teacher_id

    # ========== NEW SCHEDULING METHODS ==========
    
    def get_upcoming_sessions(self, minutes_ahead: int = 10) -> List[Dict]:
        """Get sessions starting within the next X minutes"""
        try:
            current_time = datetime.now()
            future_time = current_time + timedelta(minutes=minutes_ahead)
            
            # Query courses where next_session is within our time window
            response = self.supabase.table('courses').select(
                'id, title, teacher_id, next_session, zoom_link, start_time, end_time'
            ).gte(
                'next_session', current_time.isoformat()
            ).lte(
                'next_session', future_time.isoformat()
            ).eq(
                'is_active', True
            ).execute()
            
            if response.data:
                self.logger.info(f"Found {len(response.data)} upcoming sessions")
                return response.data
            else:
                return []
                
        except Exception as e:
            self.logger.error(f"Error fetching upcoming sessions: {e}")
            return []

    def create_session(self, course_id: str, teacher_id: str, zoom_url: str, scheduled_start: datetime) -> Optional[str]:
        """Create a new session in active_sessions table"""
        try:
            response = self.supabase.table('active_sessions').insert({
                'course_id': course_id,
                'teacher_id': teacher_id,
                'zoom_meeting_url': zoom_url,
                'scheduled_start_time': scheduled_start.isoformat(),
                'status': 'scheduled'
            }).execute()
            
            if response.data and len(response.data) > 0:
                session_id = response.data[0]['id']
                self.logger.info(f"Created session: {session_id} for course: {course_id}")
                return session_id
            else:
                self.logger.error("Failed to create session")
                return None
                
        except Exception as e:
            self.logger.error(f"Error creating session: {e}")
            return None

    def get_available_agents(self) -> List[Dict]:
        """Get all agents with status 'idle'"""
        try:
            response = self.supabase.table('agent_instances').select('*').eq(
                'status', 'idle'
            ).execute()
            
            if response.data:
                self.logger.info(f"Found {len(response.data)} available agents")
                return response.data
            else:
                self.logger.info("No available agents found")
                return []
                
        except Exception as e:
            self.logger.error(f"Error fetching available agents: {e}")
            return []

    def assign_agent_to_session(self, session_id: str, agent_id: str) -> bool:
        """Assign an agent to a session and update both tables"""
        try:
            # Update the session with agent_id
            session_response = self.supabase.table('active_sessions').update({
                'agent_id': agent_id,
                'status': 'agent_assigned'
            }).eq('id', session_id).execute()
            
            # Update the agent status to busy
            agent_response = self.supabase.table('agent_instances').update({
                'status': 'busy'
            }).eq('id', agent_id).execute()
            
            if session_response.data and agent_response.data:
                self.logger.info(f"Assigned agent {agent_id} to session {session_id}")
                return True
            else:
                self.logger.error("Failed to assign agent to session")
                return False
                
        except Exception as e:
            self.logger.error(f"Error assigning agent: {e}")
            return False

    def update_session_status(self, session_id: str, status: str, actual_start_time: Optional[datetime] = None, actual_end_time: Optional[datetime] = None) -> bool:
        """Update session status and timing"""
        try:
            update_data = {'status': status}
            
            if actual_start_time:
                update_data['actual_start_time'] = actual_start_time.isoformat()
            if actual_end_time:
                update_data['actual_end_time'] = actual_end_time.isoformat()
            
            response = self.supabase.table('active_sessions').update(update_data).eq(
                'id', session_id
            ).execute()
            
            if response.data:
                self.logger.info(f"Updated session {session_id} status to {status}")
                return True
            else:
                return False
                
        except Exception as e:
            self.logger.error(f"Error updating session status: {e}")
            return False

    def release_agent(self, agent_id: str) -> bool:
        """Release agent back to idle status"""
        try:
            response = self.supabase.table('agent_instances').update({
                'status': 'idle'
            }).eq('id', agent_id).execute()
            
            if response.data:
                self.logger.info(f"Released agent {agent_id} back to idle")
                return True
            else:
                return False
                
        except Exception as e:
            self.logger.error(f"Error releasing agent: {e}")
            return False

    def get_session_by_id(self, session_id: str) -> Optional[Dict]:
        """Get session details by ID"""
        try:
            response = self.supabase.table('active_sessions').select('*').eq(
                'id', session_id
            ).execute()
            
            if response.data and len(response.data) > 0:
                return response.data[0]
            return None
            
        except Exception as e:
            self.logger.error(f"Error fetching session: {e}")
            return None

    def get_active_sessions(self) -> List[Dict]:
        """Get all currently active sessions"""
        try:
            response = self.supabase.table('active_sessions').select('*').in_(
                'status', ['agent_assigned', 'joining_zoom', 'active']
            ).execute()
            
            if response.data:
                return response.data
            return []
            
        except Exception as e:
            self.logger.error(f"Error fetching active sessions: {e}")
            return []

    # ========== EXISTING METHODS (UPDATED) ==========

    def get_teacher_school_id(self, teacher_id: Optional[str] = None) -> Optional[str]:
        """Get school_id for a specific teacher"""
        try:
            target_teacher_id = teacher_id or self.teacher_id
            if not target_teacher_id:
                self.logger.error("No teacher_id provided")
                return None
                
            response = self.supabase.table('users').select('school_id').eq(
                'id', target_teacher_id
            ).execute()
            
            if response.data and len(response.data) > 0:
                school_id = response.data[0]['school_id']
                self.logger.info(f"Found school_id: {school_id} for teacher: {target_teacher_id}")
                return school_id
            else:
                self.logger.warning(f"No school_id found for teacher: {target_teacher_id}")
                return None
                
        except Exception as e:
            self.logger.error(f"Error fetching teacher school_id: {e}")
            return None

    def get_teacher_courses(self, teacher_id: Optional[str] = None, school_id: Optional[str] = None) -> List[Dict]:
        """Get all courses for a specific teacher"""
        try:
            target_teacher_id = teacher_id or self.teacher_id
            if not target_teacher_id:
                self.logger.error("No teacher_id provided")
                return []
                
            query = self.supabase.table('courses').select('*').eq(
                'teacher_id', target_teacher_id
            )
            
            if school_id:
                query = query.eq('school_id', school_id)
                
            response = query.execute()
            
            if response.data:
                self.logger.info(f"Found {len(response.data)} courses for teacher")
                return response.data
            else:
                self.logger.warning(f"No courses found for teacher: {target_teacher_id}")
                return []
                
        except Exception as e:
            self.logger.error(f"Error fetching teacher courses: {e}")
            return []

    def get_all_teacher_lessons_with_courses(self, teacher_id: Optional[str] = None) -> Dict:
        """Get all lessons organized by courses for a specific teacher"""
        try:
            target_teacher_id = teacher_id or self.teacher_id
            if not target_teacher_id:
                return {'error': 'No teacher_id provided'}
                
            # Step 1: Get teacher's school_id
            school_id = self.get_teacher_school_id(target_teacher_id)
            if not school_id:
                return {'error': 'No school_id found for teacher'}
            
            # Step 2: Get teacher's courses
            courses = self.get_teacher_courses(target_teacher_id, school_id)
            if not courses:
                return {'error': 'No courses found for teacher'}
            
            # Step 3: Get lessons for each course
            teacher_data = {
                'teacher_id': target_teacher_id,
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

    def get_teacher_info(self, teacher_id: Optional[str] = None) -> Dict:
        """Get basic teacher information"""
        try:
            target_teacher_id = teacher_id or self.teacher_id
            if not target_teacher_id:
                return {'error': 'No teacher_id provided'}
                
            response = self.supabase.table('users').select('id, school_id').eq(
                'id', target_teacher_id
            ).execute()
            
            if response.data and len(response.data) > 0:
                user_data = response.data[0]
                user_data['name'] = user_data.get('name', 'Teacher')
                user_data['email'] = user_data.get('email', 'N/A')
                return user_data
            else:
                return {'error': 'Teacher not found'}
                
        except Exception as e:
            self.logger.error(f"Error fetching teacher info: {e}")
            try:
                response = self.supabase.table('users').select('*').eq(
                    'id', target_teacher_id
                ).execute()
                
                if response.data and len(response.data) > 0:
                    user_data = response.data[0]
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

    # ========== EXISTING METHODS (UNCHANGED) ==========

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

    def is_pdf_resource(self, resource: str) -> bool:
        """Check if resource is a direct PDF file"""
        if not resource or resource.strip().lower() in ['null', 'none', '', 'empty']:
            return False
        
        resource_str = str(resource).strip().lower()
        return (resource_str.startswith(('http://', 'https://')) and 
                resource_str.endswith('.pdf'))

    def get_lessons_with_pdf_resources(self, limit: int = 100, teacher_id: Optional[str] = None) -> List[Dict]:
        """Get lessons with PDF resources for a specific teacher"""
        try:
            target_teacher_id = teacher_id or self.teacher_id
            if not target_teacher_id:
                self.logger.error("No teacher_id provided")
                return []
                
            teacher_data = self.get_all_teacher_lessons_with_courses(target_teacher_id)
            
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

    def get_lesson_by_id(self, lesson_id: str) -> Optional[Dict]:
        """Get specific lesson by ID"""
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

    # ========== AGENT MANAGEMENT HELPERS ==========

    def initialize_agents(self, agent_names: List[str]) -> bool:
        """Initialize agents in the database if they don't exist"""
        try:
            for agent_name in agent_names:
                # Check if agent exists
                existing = self.supabase.table('agent_instances').select('id').eq(
                    'agent_name', agent_name
                ).execute()
                
                if not existing.data:
                    # Create new agent
                    self.supabase.table('agent_instances').insert({
                        'agent_name': agent_name,
                        'status': 'idle'
                    }).execute()
                    self.logger.info(f"Initialized agent: {agent_name}")
            
            return True
            
        except Exception as e:
            self.logger.error(f"Error initializing agents: {e}")
            return False

    def create_agent_pool(self, count: int = 5) -> bool:
        """Create a pool of agents with default names"""
        try:
            agent_names = [f'Agent-{i:03d}' for i in range(1, count + 1)]
            return self.initialize_agents(agent_names)
        except Exception as e:
            self.logger.error(f"Error creating agent pool: {e}")
            return False

    # Legacy method for backward compatibility
    def get_lessons_with_resources(self, limit: int = 100) -> List[Dict]:
        """Legacy method - now calls get_lessons_with_pdf_resources"""
        return self.get_lessons_with_pdf_resources(limit)