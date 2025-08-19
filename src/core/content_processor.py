import PyPDF2
import io
import requests
from openai import OpenAI
import os
from typing import Optional
import logging
from pathlib import Path
from datetime import datetime

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

    def create_lecture_script(self, pdf_url: str, lesson_title: str) -> dict:
        """Convert PDF content into a structured lecture script"""
        try:
            if not self.is_valid_pdf_url(pdf_url):
                raise ValueError(f"Invalid PDF URL: {pdf_url}")

            if not os.getenv("OPENAI_API_KEY"):
                raise ValueError('OpenAI API key not provided')

            pdf_bytes = self.download_pdf_from_url(pdf_url)
            text_content = self.extract_text_from_pdf(pdf_bytes)
            
            if len(text_content) < 100:
                raise ValueError("PDF content too short")

            self.logger.info("Generating lecture script with OpenAI...")
            response = self.openai_client.chat.completions.create(
                model="gpt-4",
                messages=[
                    {
                        "role": "system",
                        "content": """You are an expert lecturer creating an engaging presentation script. 

                        Create a well-structured lecture script that:
                        1. Has a compelling introduction (2-3 minutes)
                        2. Breaks complex topics into digestible segments with clear transitions  
                        3. Includes relevant examples and analogies
                        4. Has engaging delivery with natural speech patterns
                        5. Ends with a strong summary and key takeaways
                        6. Is designed to be spoken aloud naturally
                        7. Total duration should be 20-30 minutes

                        Format with timing markers like [2:00], [5:00] etc. for different sections.
                        Make it conversational and engaging for students."""
                    },
                    {
                        "role": "user",
                        "content": f"""Create a lecture script for "{lesson_title}" based on this content:\n\n{text_content[:8000]}"""
                    }
                ],
                max_tokens=3500,
                temperature=0.7
            )
            
            script = response.choices[0].message.content
            if not script:
                raise ValueError("OpenAI returned empty script")
            
            script_filename = f"lecture_script_{lesson_title.replace(' ', '_').replace('/', '_')}.txt"
            script_file = self.scripts_dir / script_filename
            
            with open(script_file, 'w', encoding='utf-8') as f:
                f.write(f"LECTURE SCRIPT: {lesson_title}\n")
                f.write(f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
                f.write(f"PDF Source: {pdf_url}\n")
                f.write("="*80 + "\n\n")
                f.write(script)
            
            self.logger.info(f"Generated lecture script: {len(script)} characters")
            self.logger.info(f"Script saved to: {script_file}")
            
            return {
                'script': script,
                'script_file': str(script_file),
                'length': len(script),
                'lesson_title': lesson_title,
                'pdf_source': pdf_url
            }
            
        except Exception as e:
            self.logger.error(f"Error creating lecture script: {e}")
            raise Exception(f"Failed to create lecture script: {str(e)}")

    def analyze_pdf_content(self, pdf_url: str) -> dict:
        """Analyze PDF content and return summary"""
        try:
            pdf_bytes = self.download_pdf_from_url(pdf_url)
            text_content = self.extract_text_from_pdf(pdf_bytes)
            
            word_count = len(text_content.split())
            char_count = len(text_content)
            reading_time_minutes = word_count / 200
            
            return {
                'word_count': word_count,
                'character_count': char_count,
                'estimated_reading_time': f"{reading_time_minutes:.1f} minutes",
                'content_preview': text_content[:500] + "..." if len(text_content) > 500 else text_content
            }
            
        except Exception as e:
            self.logger.error(f"Error analyzing PDF: {e}")
            return {'error': str(e)}