# script_pipeline.py
import os
from typing import Optional
from src.integrations.supabase_client import SupabaseClient
from src.core.content_processor import ContentProcessor


BUCKET_NAME = os.getenv("SCRIPTS_BUCKET", "lecture-scripts")  # create this bucket in Supabase

def generate_and_upload_scripts_for_teacher(
    teacher_id: str,
    audience: str = "middle school (ages 11–14)",
    language: str = "English",
    sign_urls: bool = True,
) -> dict:
    """
    For the given teacher:
      - fetch teacher & their courses
      - for each lesson with one or more PDF 'resources'
      - create a student-friendly script PDF
      - upload to storage at lecture-scripts/{teacher}/{course}/{lesson}.pdf
      - record in prepared_lessons table
    Returns a summary payload with uploaded paths and optional signed URLs.
    """
    sb = SupabaseClient(teacher_id=teacher_id)
    cp = ContentProcessor()

    teacher = sb.get_teacher_info()
    if teacher.get("error"):
        return {"error": teacher["error"]}

    school_id = sb.get_teacher_school_id()
    if not school_id:
        return {"error": "No school_id found for teacher"}

    courses = sb.get_teacher_courses(school_id)
    if not courses:
        return {"error": "No courses found for teacher"}

    results = {
        "teacher_id": teacher_id,
        "teacher_name": teacher.get("name"),
        "school_id": school_id,
        "bucket": BUCKET_NAME,
        "items": [],  # list of dicts {course_id, lesson_id, uploaded_path, signed_url?}
        "errors": [],
    }

    for course in courses:
        course_id = course["id"]
        course_title = course.get("title") or f"course-{course_id}"

        lessons = sb.get_lessons_with_pdf_resources(course_id)
        for lesson in lessons:
            lesson_id = lesson["id"]
            lesson_title = lesson.get("title") or f"lesson-{lesson_id}"

            for idx, pdf_url in enumerate(lesson.get("pdf_urls", []), start=1):
                try:
                    pack = cp.generate_script_pdf_bytes(
                        pdf_source_url=pdf_url,
                        lesson_title=lesson_title,
                        teacher_name=teacher.get("name"),
                        audience=audience,
                        language=language,
                    )

                    path = f"{teacher_id}/{course_id}/{lesson_id}/script_{idx}.pdf"
                    upload_info = sb.upload_pdf_to_bucket(
                        bucket=BUCKET_NAME,
                        pdf_bytes=pack["pdf_bytes"],
                        path=path,
                        upsert=True,
                    )

                    # Get the URL for the uploaded file
                    file_url = None
                    if sign_urls:
                        file_url = sb.create_signed_url(BUCKET_NAME, upload_info["path"], expires_in=60 * 60 * 24 * 7)
                    else:
                        file_url = sb.get_public_url(BUCKET_NAME, upload_info["path"])

                    # THIS IS THE MISSING PIECE - Record in prepared_lessons table
                    if file_url:
                        try:
                            db_record = sb.record_prepared_lesson(lesson_id, file_url)
                            print(f"Successfully recorded in prepared_lessons: {db_record}")
                        except Exception as db_error:
                            print(f"Failed to record in prepared_lessons: {db_error}")
                            results["errors"].append({
                                "course_id": course_id,
                                "lesson_id": lesson_id,
                                "pdf_url": pdf_url,
                                "error": f"DB insert failed: {str(db_error)}",
                            })

                    item = {
                        "course_id": course_id,
                        "course_title": course_title,
                        "lesson_id": lesson_id,
                        "lesson_title": lesson_title,
                        "source_pdf": pdf_url,
                        "uploaded_path": upload_info["path"],
                    }
                    if file_url:
                        item["signed_url"] = file_url

                    results["items"].append(item)

                except Exception as e:
                    results["errors"].append({
                        "course_id": course_id,
                        "lesson_id": lesson_id,
                        "pdf_url": pdf_url,
                        "error": str(e),
                    })

    return results

if __name__ == "__main__":
    # quick manual run (e.g., `python script_pipeline.py`)
    import sys
    if len(sys.argv) < 2:
        print("Usage: python script_pipeline.py <teacher_id>")
        sys.exit(1)
    teacher_id = sys.argv[1]
    summary = generate_and_upload_scripts_for_teacher(teacher_id)
    print(summary)