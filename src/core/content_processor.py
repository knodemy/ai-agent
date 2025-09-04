import PyPDF2
import io
import requests
from openai import OpenAI
import os
from typing import Optional
import logging
from pathlib import Path
from datetime import datetime
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import cm
from textwrap import wrap

class ContentProcessor:
    def __init__(self):
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key or api_key == "your_openai_api_key":
            raise ValueError("Please set your OPENAI_API_KEY in .env file")
        self.openai_client = OpenAI(api_key=api_key)
        self.logger = logging.getLogger(__name__)
        
        self.scripts_dir = Path("temp/scripts")
        self.scripts_dir.mkdir(parents=True, exist_ok=True)

    def is_valid_pdf_url(self, pdf_url: str) -> bool:
        """Check if URL is a valid direct PDF URL"""
        if not pdf_url or pdf_url == 'NULL':
            return False
        
        url_lower = pdf_url.lower().strip()
        
        # Only accept direct PDF URLs
        return (url_lower.startswith(('http://', 'https://')) and 
                url_lower.endswith('.pdf'))

    def download_pdf_from_url(self, pdf_url: str) -> bytes:
        """Download PDF from direct URL"""
        try:
            self.logger.info(f"Downloading PDF from: {pdf_url}")
            
            if not self.is_valid_pdf_url(pdf_url):
                raise ValueError(f"Invalid PDF URL: {pdf_url}")
            
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
                'Accept': 'application/pdf,application/octet-stream,*/*'
            }
            
            response = requests.get(pdf_url, headers=headers, timeout=30)
            response.raise_for_status()
            
            if not response.content:
                raise ValueError("Empty PDF content")
            
            if not response.content.startswith(b'%PDF'):
                raise ValueError("Downloaded content is not a valid PDF")
            
            self.logger.info(f"Downloaded PDF: {len(response.content)} bytes")
            return response.content
            
        except Exception as e:
            self.logger.error(f"Error downloading PDF: {e}")
            raise Exception(f"Failed to download PDF: {str(e)}")

    def extract_text_from_pdf(self, pdf_bytes: bytes) -> str:
        """Extract text from PDF bytes"""
        try:
            pdf_file = io.BytesIO(pdf_bytes)
            pdf_reader = PyPDF2.PdfReader(pdf_file)
            
            text = ""
            for page_num, page in enumerate(pdf_reader.pages):
                try:
                    page_text = page.extract_text()
                    if page_text:
                        text += f"\n--- Page {page_num + 1} ---\n{page_text}\n"
                except Exception as e:
                    self.logger.warning(f"Could not extract text from page {page_num + 1}: {e}")
            
            if not text.strip():
                raise ValueError("No text could be extracted from PDF")
            
            self.logger.info(f"Extracted {len(text)} characters from {len(pdf_reader.pages)} pages")
            return text.strip()
            
        except Exception as e:
            self.logger.error(f"Error extracting PDF text: {e}")
            raise Exception(f"Failed to extract PDF text: {str(e)}")

    def create_student_friendly_script(self, source_text: str, lesson_title: str,
                                       audience: str = "middle school (ages 11–14)",
                                       language: str = "English",
                                       duration_minutes: tuple[int, int] = (35, 40)) -> str:
        if not os.getenv("OPENAI_API_KEY"):
            raise ValueError('OpenAI API key not provided')

        lo, hi = duration_minutes
        system_prompt = system_prompt = f"""
                You are an expert teacher. Create a highly detailed, engaging 40-minute lecture script for {audience} students.
                Must be in {language}. Keep the tone warm, clear, and conversational. Avoid jargon unless you define it.

                STRUCTURE REQUIREMENTS:
                1) Opening Hook (3-5 minutes): Engaging introduction with real-world connection
                2) Learning Objectives (2 minutes): Clear, student-friendly goals
                3) Main Content Sections (25-30 minutes): Break into 3-4 major sections with:
                - Clear transitions between sections
                - Interactive elements every 5-7 minutes
                - Real-world examples and analogies
                - Visual descriptions ("imagine this..." or "picture this...")
                4) Practice/Application (5-7 minutes): Student activities or examples
                5) Recap & Takeaways (3-5 minutes): Summarize key points

                TIMING & ENGAGEMENT:
                - Include timing markers like [5:00], [12:30], [25:00], etc.
                - "Check-in" questions every 5-7 minutes to maintain engagement
                - Include "pause moments" for student reflection
                - Suggest when to show examples, diagrams, or interactive elements
                - Total spoken duration should be {lo}–{hi} minutes

                CONTENT DEPTH:
                - Provide detailed explanations with multiple examples
                - Include step-by-step breakdowns of complex concepts
                - Add historical context or interesting facts where relevant
                - Suggest extension activities for advanced students
                - Include common misconceptions and how to address them

                FORMAT:
                - Use clear section headers
                - Include speaker notes in [brackets] for teaching tips
                - Bullet points for key concepts
                - Suggested discussion questions
                - Recommended visual aids or demonstrations

                IMPORTANT: Every script MUST include all 5 section headers with their exact timing markers as shown above.
                """
        user_prompt = f'Lesson Title: "{lesson_title}"\n\nBase the script on this content (reorganize/simplify as needed):\n\n{source_text[:8000]}'

        resp = self.openai_client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[{"role": "system", "content": system_prompt},
                      {"role": "user", "content": user_prompt}],
            max_tokens=3000,
            temperature=0.7,
        )
        script = resp.choices[0].message.content if resp.choices else ""
        if not script:
            raise ValueError("OpenAI returned an empty script")
        return script

    def _render_text_to_pdf(self, title: str, subtitle_lines: list[str], body: str,
                            page_size=A4, margins_cm: float = 2.0,
                            font_name: str = "Helvetica", font_size: int = 11,
                            heading_font_size: int = 16) -> bytes:
        buf = io.BytesIO()
        c = canvas.Canvas(buf, pagesize=page_size)
        width, height = page_size
        margin = margins_cm * cm
        usable_width = width - 2 * margin
        y = height - margin

        c.setFont(font_name, heading_font_size)
        c.drawString(margin, y, title[:120]); y -= 0.8 * cm
        c.setFont(font_name, 10)
        for line in subtitle_lines:
            c.drawString(margin, y, line[:160]); y -= 0.55 * cm
        y -= 0.3 * cm
        c.line(margin, y, width - margin, y); y -= 0.6 * cm

        c.setFont(font_name, font_size)
        line_height = 0.52 * cm
        from textwrap import wrap
        paragraphs = body.splitlines()
        for para in paragraphs:
            if not para.strip():
                y -= line_height
                if y <= margin:
                    c.showPage(); y = height - margin; c.setFont(font_name, font_size)
                continue
            wrapped = wrap(para, width=int(usable_width / (font_size * 0.5)))
            for line in wrapped:
                if y <= margin:
                    c.showPage(); y = height - margin; c.setFont(font_name, font_size)
                c.drawString(margin, y, line); y -= line_height

        c.showPage(); c.save(); buf.seek(0)
        return buf.read()

    def generate_script_pdf_bytes(self, pdf_source_url: str, lesson_title: str,
                                  teacher_name: str, audience: str = "middle school (ages 11–14)",
                                  language: str = "English") -> dict:
        if not self.is_valid_pdf_url(pdf_source_url):
            raise ValueError(f"Invalid PDF URL: {pdf_source_url}")

        src_bytes = self.download_pdf_from_url(pdf_source_url)
        extracted = self.extract_text_from_pdf(src_bytes)
        if len(extracted) < 100:
            raise ValueError("PDF content too short to build a meaningful script")

        script_text = self.create_student_friendly_script(
            source_text=extracted,
            lesson_title=lesson_title,
            audience=audience,
            language=language,
        )

        now = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
        subtitle = [f"Generated for: {teacher_name or 'Teacher'}",
                    f"Source: {pdf_source_url}",
                    f"Generated: {now}"]
        pdf_bytes = self._render_text_to_pdf(
            title=f"Lecture Script: {lesson_title}",
            subtitle_lines=subtitle,
            body=script_text,
        )
        return {"pdf_bytes": pdf_bytes, "script_text": script_text,
                "meta": {"lesson_title": lesson_title,
                        "source_url": pdf_source_url,
                        "generated_at": now}}