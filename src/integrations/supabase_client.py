# supabase_client.py
from supabase import create_client, Client
from datetime import datetime, timedelta
import os
from dotenv import load_dotenv
from typing import List, Dict, Optional
import logging
import json
from typing import Tuple

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
            # Correct usage for Supabase Python client
            response = self.supabase.table('lessons').select('*').eq(
                'course_id', course_id
            ).order('created_at', desc=True).execute()  # descending order
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

    def _extract_pdf_urls(self, resources_val) -> list[str]:
        """
        Returns a list of direct PDF URLs from the 'resources' column.
        Accepts JSON (list/dict) or raw string. Filters to *.pdf only.
        """
        urls: list[str] = []

        if not resources_val or str(resources_val).strip().upper() == "NULL":
            return urls

        try:
            # Try to parse JSON first
            parsed = resources_val if isinstance(resources_val, (list, dict)) else json.loads(resources_val)
            if isinstance(parsed, list):
                for item in parsed:
                    if isinstance(item, str) and item.lower().endswith(".pdf"):
                        urls.append(item)
                    elif isinstance(item, dict):
                        # Common shapes: {"url": "...", "type": "pdf"} etc.
                        u = item.get("url") or item.get("href") or item.get("path")
                        if isinstance(u, str) and u.lower().endswith(".pdf"):
                            urls.append(u)
            elif isinstance(parsed, dict):
                # e.g. {"url":"...pdf"}
                u = parsed.get("url") or parsed.get("href") or parsed.get("path")
                if isinstance(u, str) and u.lower().endswith(".pdf"):
                    urls.append(u)
        except Exception:
            # Not JSON — treat as string with possible separators
            s = str(resources_val)
            parts = [p.strip() for p in s.replace(",", "\n").splitlines()]
            for p in parts:
                if p.lower().endswith(".pdf") and p.lower().startswith(("http://", "https://")):
                    urls.append(p)

        # de-dup
        return list(dict.fromkeys(urls))

    def get_lessons_with_pdf_resources(self, course_id: str) -> list[dict]:
        """
        Returns lessons for the course but only those with 1+ direct PDF URLs,
        adding a 'pdf_urls' list to each lesson object.
        """
        lessons = self.get_course_lessons(course_id)
        with_pdfs = []
        for lesson in lessons:
            pdf_urls = self._extract_pdf_urls(lesson.get("resources"))
            if pdf_urls:
                lesson["pdf_urls"] = pdf_urls
                with_pdfs.append(lesson)
        return with_pdfs

    # ---------- Storage helpers ----------

    def upload_pdf_to_bucket(
        self,
        bucket: str,
        pdf_bytes: bytes,
        path: str,
        upsert: bool = True,
        content_type: str = "application/pdf",
    ) -> dict:
        """
        Uploads bytes to Supabase Storage. Handles different supabase-py signatures.
        """
        # Convert bool to the string formats the API expects
        upsert_str = "true" if upsert else "false"

        # Try common option key variants (different client versions accept different keys)
        option_sets = [
            {"content-type": content_type, "x-upsert": upsert_str},         # newer servers like x-upsert
            {"content-type": content_type, "upsert": upsert_str},           # some clients accept upsert as a string
            {"contentType": content_type, "upsert": upsert_str},            # camelCase variant
        ]

        last_err = None
        for opts in option_sets:
            try:
                # Prefer positional signature first (most reliable across versions)
                res = self.supabase.storage.from_(bucket).upload(path, pdf_bytes, opts)
                if isinstance(res, dict) and res.get("error"):
                    raise RuntimeError(res["error"])
                return {"bucket": bucket, "path": path}
            except Exception as e:
                last_err = e
                continue

        self.logger.error(f"Upload error for {path}: {last_err}")
        raise last_err

    def create_signed_url(
        self, bucket: str, path: str, expires_in: int = 60 * 60 * 24
    ) -> Optional[str]:
        """
        Returns a time-limited signed URL so frontend can fetch the PDF.
        """
        try:
            data = self.supabase.storage.from_(bucket).create_signed_url(path, expires_in)
            return data.get("signedURL") or data.get("signed_url")
        except Exception as e:
            self.logger.error(f"Signed URL error for {path}: {e}")
            return None

    def get_public_url(self, bucket: str, path: str) -> str | None:
        try:
            data = self.supabase.storage.from_(bucket).get_public_url(path)
            return data.get("publicURL") or data.get("public_url")
        except Exception as e:
            self.logger.error(f"Public URL error for {path}: {e}")
            return None

    def record_prepared_lesson(self, lesson_id: str, url: str) -> dict:
        """
        Upsert a row per (lesson_id, url). agent_id stays NULL.
        """

        payload = {
            "lesson_id": lesson_id,
            "teacher_id": self.teacher_id,
            "agent_id": None,
            "url": url,
        }
        try:
            print(f"Inserting into prepared_lessons: {payload}")
            res = self.supabase.table("prepared_lessons").upsert(
                payload, on_conflict="lesson_id,url"
            ).execute()
            print(f"Insert result: {res}")  # Debugging
            return res.data[0] if res.data else payload
        except Exception as e:
            print("no insert")
            self.logger.error(f"DB insert error (prepared_lessons): {e}")
            raise


    def upload_audio_to_bucket(
        self,
        bucket: str,
        audio_bytes: bytes,
        path: str,
        upsert: bool = True,
        content_type: str = "audio/mpeg",
    ) -> dict:
        """
        Uploads audio bytes to Supabase Storage. 
        Same as upload_pdf_to_bucket but with audio-specific content type.
        """
        # Convert bool to the string formats the API expects
        upsert_str = "true" if upsert else "false"

        # Try common option key variants (different client versions accept different keys)
        option_sets = [
            {"content-type": content_type, "x-upsert": upsert_str},
            {"content-type": content_type, "upsert": upsert_str},
            {"contentType": content_type, "upsert": upsert_str},
        ]

        last_err = None
        for opts in option_sets:
            try:
                res = self.supabase.storage.from_(bucket).upload(path, audio_bytes, opts)
                if isinstance(res, dict) and res.get("error"):
                    raise RuntimeError(res["error"])
                return {"bucket": bucket, "path": path}
            except Exception as e:
                last_err = e
                continue

        self.logger.error(f"Audio upload error for {path}: {last_err}")
        raise last_err

    def record_prepared_audio(self, lesson_id: str, audio_url: str) -> dict:
        """
        Record a prepared audio file in the database.
        You might want to create a separate table for audio files or 
        add an audio_url column to prepared_lessons.
        """
        payload = {
            "lesson_id": lesson_id,
            "teacher_id": self.teacher_id,
            "agent_id": None,
            "audio_url": audio_url,  # Consider adding this column to your table
            "type": "audio",  # To distinguish from script PDFs
        }
        
        try:
            # Option 1: If you add an audio_url column to prepared_lessons table
            res = self.supabase.table("prepared_lessons").update(
                {"audio_url": audio_url}
            ).eq("lesson_id", lesson_id).eq("teacher_id", self.teacher_id).execute()
            
            # Option 2: If you create a separate audio table (recommended)
            # res = self.supabase.table("prepared_audio").upsert(
            #     payload, on_conflict="lesson_id,teacher_id"
            # ).execute()
            
            return res.data[0] if res.data else payload
        except Exception as e:
            self.logger.error(f"DB insert error (prepared_audio): {e}")
            raise

    def get_prepared_lessons_for_audio_generation(self, course_id: str, date: str) -> List[Dict]:
        """
        Get prepared lessons that need audio generation.
        Returns lessons that have script PDFs but no audio files.
        """
        try:
            # Get all prepared lessons for this course
            response = self.supabase.table('prepared_lessons').select(
                'lesson_id, url'
            ).eq('teacher_id', self.teacher_id).execute()
            
            if not response.data:
                return []
            
            # Filter for script PDFs that don't have corresponding audio
            script_lessons = []
            for lesson in response.data:
                url = lesson.get('url', '')
                if url and 'script.pdf' in url and date in url:
                    # Check if audio already exists
                    audio_exists = self.check_if_audio_exists(lesson['lesson_id'], date)
                    if not audio_exists:
                        script_lessons.append(lesson)
            
            return script_lessons
            
        except Exception as e:
            self.logger.error(f"Error fetching prepared lessons for audio: {e}")
            return []

    def check_if_audio_exists(self, lesson_id: str, date: str) -> bool:
        """Check if audio file already exists for a lesson."""
        try:
            # Check in prepared_lessons table for audio_url
            response = self.supabase.table('prepared_lessons').select(
                'audio_url'
            ).eq('lesson_id', lesson_id).eq('teacher_id', self.teacher_id).execute()
            
            if response.data and response.data[0].get('audio_url'):
                return True
            
            # Alternatively, check in storage bucket directly
            audio_bucket = "lecture-audios"
            expected_path = f"{self.teacher_id}/{lesson_id}/{date}/{lesson_id}_audio.mp3"
            
            try:
                file_info = self.supabase.storage.from_(audio_bucket).list(
                    path=f"{self.teacher_id}/{lesson_id}/{date}"
                )
                audio_files = [f for f in file_info if f.get('name', '').endswith('_audio.mp3')]
                return len(audio_files) > 0
            except:
                return False
                
        except Exception as e:
            self.logger.error(f"Error checking audio existence: {e}")
            return False