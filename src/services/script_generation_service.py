import os
import json
from datetime import datetime
from supabase_client import SupabaseClient
from content_processor import ContentProcessor

# Set up environment variables (Supabase client and OpenAI setup)
BUCKET_NAME = "lecture-scripts"  # Specify your bucket name here (default)

class ScriptGenerationService:
    def __init__(self, teacher_id: str):
        self.teacher_id = teacher_id
        self.sb = SupabaseClient(teacher_id)
        self.cp = ContentProcessor()

    def generate_and_upload_script(self, lesson_id: str, lesson_title: str, pdf_url: str) -> dict:
        """
        Generate a student-friendly script from a PDF URL and upload it to Supabase Storage.
        Then store the result in the prepared_lessons table.
        """
        try:
            # Step 1: Download PDF and extract text
            print(f"Processing lesson {lesson_title} for teacher {self.teacher_id}")
            script_data = self.cp.generate_script_pdf_bytes(
                pdf_source_url=pdf_url,
                lesson_title=lesson_title,
                teacher_name=self.teacher_id  # Can be fetched from the teacher's details
            )

            # Step 2: Upload the generated script to Supabase Storage
            path = f"{self.teacher_id}/{lesson_id}/script_1.pdf"
            upload_info = self.sb.upload_pdf_to_bucket(
                bucket=BUCKET_NAME,
                pdf_bytes=script_data["pdf_bytes"],
                path=path,
                upsert=True
            )

            # Step 3: Get the URL (either public or signed)
            file_url = self.sb.get_public_url(BUCKET_NAME, upload_info["path"])

            # Step 4: Record the uploaded script in the database
            print("this is lesson id: ", lesson_id)
            db_row = self.sb.record_prepared_lesson(lesson_id=lesson_id, url=file_url)

            # Step 5: Return the successful result
            return {
                "lesson_id": lesson_id,
                "lesson_title": lesson_title,
                "url": file_url,
                "db_row_id": db_row.get("id"),
                "message": "Script generated and uploaded successfully."
            }
        except Exception as e:
            print(f"Error generating and uploading script: {e}")
            return {"error": str(e)}

    def generate_scripts_for_teacher(self):
        """
        Fetch all lessons for the teacher, generate scripts, upload, and record them in the database.
        """
        try:
            # Step 1: Get teacher info and courses
            teacher_info = self.sb.get_teacher_info()
            if "error" in teacher_info:
                return teacher_info  # If teacher info is not found
            
            # Get the teacher's school_id to fetch courses
            school_id = self.sb.get_teacher_school_id()
            if not school_id:
                return {"error": "No school_id found for teacher"}

            # Fetch the teacher's courses
            courses = self.sb.get_teacher_courses(school_id)
            if not courses:
                return {"error": "No courses found for teacher"}

            # Step 2: Process each course and its lessons
            results = {"items": [], "errors": []}

            for course in courses:
                course_id = course["id"]
                course_title = course["title"]

                # Get lessons for the course
                lessons = self.sb.get_lessons_with_pdf_resources(course_id)
                for lesson in lessons:
                    lesson_id = lesson["id"]
                    lesson_title = lesson.get("title", f"lesson-{lesson_id}")
                    pdf_urls = lesson.get("pdf_urls", [])

                    for idx, pdf_url in enumerate(pdf_urls, start=1):
                        # Step 3: Generate and upload the script for each lesson
                        response = self.generate_and_upload_script(lesson_id, lesson_title, pdf_url)
                        
                        # Record results
                        if "error" in response:
                            results["errors"].append({
                                "course_id": course_id,
                                "lesson_id": lesson_id,
                                "pdf_url": pdf_url,
                                "error": response["error"]
                            })
                        else:
                            results["items"].append(response)

            return results

        except Exception as e:
            print(f"Error in generating scripts for teacher: {e}")
            return {"error": str(e)}

# Main execution (for direct running/testing)
if __name__ == "__main__":
    teacher_id = "16c538d3-8e32-45cb-bc5d-2288e8036559"  # Replace with the desired teacher's ID
    service = ScriptGenerationService(teacher_id)
    
    # Run script generation and upload for all lessons of this teacher
    result = service.generate_scripts_for_teacher()
    print(json.dumps(result, indent=2))
