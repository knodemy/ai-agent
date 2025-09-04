import os
import io
import re
import queue
import threading
import time
import json
import logging
from typing import Optional, List, Dict, Tuple
from pathlib import Path
from dataclasses import dataclass
import openai
from openai import OpenAI
import requests
from datetime import datetime
import PyPDF2
import soundfile as sf
import numpy as np
from scipy.io import wavfile
import tempfile

try:
    from src.integrations.supabase_client import SupabaseClient
except Exception:
    from supabase_client import SupabaseClient

logger = logging.getLogger(__name__)

@dataclass
class LectureSegment:
    title: str
    content: str
    duration_min: int
    duration_max: int
    order: int
    segment_type: str  # 'hook', 'objectives', 'content', 'practice', 'recap'

class EnhancedTimedSpeechGenerator:
    def __init__(self, openai_api_key: Optional[str] = None):
        """Initialize the enhanced timed speech generator with OpenAI API key."""
        self.openai_api_key = openai_api_key or os.getenv("OPENAI_API_KEY")
        if not self.openai_api_key or not self.openai_api_key.startswith("sk-"):
            raise ValueError("Valid OpenAI API key is required")
        
        self.openai_client = OpenAI(api_key=self.openai_api_key)
        self.logger = logging.getLogger(__name__)
        
        # Audio generation settings
        self.voice = "alloy"  # Default voice
        self.model = "tts-1"
        self.max_chars_per_chunk = 4000
        self.max_concurrent_workers = 5
        self.audio_bucket = "lecture-audios"
        self.sample_rate = 24000  # OpenAI TTS output sample rate
        
        # Create temp directory for audio processing
        self.temp_dir = Path("temp/audio_chunks")
        self.temp_dir.mkdir(parents=True, exist_ok=True)

    def set_voice(self, voice: str):
        """Set the voice for text-to-speech generation."""
        valid_voices = ["alloy", "echo", "fable", "onyx", "nova", "shimmer"]
        if voice in valid_voices:
            self.voice = voice
        else:
            logger.warning(f"Invalid voice '{voice}'. Using default 'alloy'")

    def clean_script_for_speech(self, script_text: str) -> str:
        """Clean the entire script for speech - remove only metadata, keep all lecture content."""
        lines = script_text.split('\n')
        cleaned_lines = []
        skip_metadata = True
        
        for i, line in enumerate(lines):
            line_stripped = line.strip()
            
            # Skip only the first few metadata lines
            if skip_metadata and i < 8:
                if (line_stripped.startswith('Lecture Script:') or 
                    line_stripped.startswith('Generated for:') or 
                    line_stripped.startswith('Source:') or 
                    line_stripped.startswith('Generated:') or
                    line_stripped == '' or
                    line_stripped == '---' or
                    'Generated:' in line_stripped or
                    'Source:' in line_stripped):
                    continue
                else:
                    skip_metadata = False
            
            if not skip_metadata:
                # Only remove obvious non-speech elements but keep the lecture content
                if (not line_stripped.startswith('[Speaker note') and 
                    not line_stripped.startswith('[Teaching tip') and
                    not line_stripped.startswith('[Show slide')):
                    # Clean up formatting but keep content
                    cleaned_line = re.sub(r'\*\*(.*?)\*\*', r'\1', line)  # Remove bold
                    cleaned_line = re.sub(r'\*(.*?)\*', r'\1', cleaned_line)  # Remove italic
                    cleaned_line = re.sub(r'#{1,6}\s*', '', cleaned_line)  # Remove headers
                    cleaned_lines.append(cleaned_line)
        
        return '\n'.join(cleaned_lines).strip()

    def split_script_into_natural_sections(self, script_text: str) -> List[Dict]:
        """Split script into natural sections based on headers and content structure."""
        
        # Clean the script first
        clean_script = self.clean_script_for_speech(script_text)
        
        # Look for section headers or timing patterns
        sections = []
        current_section = {"title": "Introduction", "content": "", "order": 0}
        lines = clean_script.split('\n')
        
        section_patterns = [
            r'(opening|hook|introduction)',
            r'(learning objectives|objectives|goals)',
            r'(main content|content|lesson|topic)',
            r'(practice|application|activity|exercise)',
            r'(recap|summary|conclusion|takeaway)'
        ]
        
        current_content = []
        section_order = 0
        
        for line in lines:
            line_stripped = line.strip().lower()
            
            # Check if this line indicates a new section
            is_new_section = False
            section_title = ""
            
            # Look for timing markers that indicate sections
            timing_match = re.search(r'\[([^:]*?):\s*(\d+)[-–](\d+)\s*minutes?\]', line, re.IGNORECASE)
            if timing_match:
                section_title = timing_match.group(1).strip()
                is_new_section = True
            else:
                # Look for section headers
                for pattern in section_patterns:
                    if re.search(pattern, line_stripped):
                        section_title = line.strip()
                        is_new_section = True
                        break
            
            if is_new_section and current_content:
                # Save the previous section
                current_section["content"] = '\n'.join(current_content).strip()
                if current_section["content"]:
                    sections.append(current_section.copy())
                
                # Start new section
                current_section = {
                    "title": section_title or f"Section {section_order + 1}",
                    "content": "",
                    "order": section_order
                }
                section_order += 1
                current_content = []
            else:
                # Add content to current section (skip the timing marker lines)
                if not re.search(r'\[.*?minutes?\]', line):
                    current_content.append(line)
        
        # Add the last section
        if current_content:
            current_section["content"] = '\n'.join(current_content).strip()
            if current_section["content"]:
                sections.append(current_section)
        
        # If no sections found, treat entire script as one section
        if not sections:
            sections = [{
                "title": "Complete Lecture",
                "content": clean_script,
                "order": 0
            }]
        
        self.logger.info(f"Split script into {len(sections)} sections")
        for i, section in enumerate(sections):
            self.logger.info(f"Section {i}: '{section['title']}' - {len(section['content'])} characters")
        
        return sections

    def text_to_speech_chunk(self, text: str, output_path: str) -> bool:
        """Convert a single text chunk to speech using OpenAI's TTS API."""
        try:
            if not text.strip():
                self.logger.warning("Empty text provided to TTS")
                return False
                
            response = self.openai_client.audio.speech.create(
                model=self.model,
                voice=self.voice,
                input=text.strip()
            )
            
            with open(output_path, "wb") as audio_file:
                audio_file.write(response.content)
                
            return True
        except Exception as e:
            logger.error(f"OpenAI TTS Error for chunk: {str(e)}")
            return False

    def combine_audio_files(self, audio_files: List[str], output_path: str) -> bool:
        """Combine multiple audio files using soundfile and numpy."""
        try:
            combined_audio = []
            sample_rate = None
            
            for audio_file in audio_files:
                if not os.path.exists(audio_file):
                    self.logger.warning(f"Audio file not found: {audio_file}")
                    continue
                    
                # Read audio file
                audio_data, sr = sf.read(audio_file)
                
                if sample_rate is None:
                    sample_rate = sr
                elif sr != sample_rate:
                    self.logger.warning(f"Sample rate mismatch: {sr} vs {sample_rate}")
                
                # Ensure audio is 1D (mono)
                if len(audio_data.shape) > 1:
                    audio_data = np.mean(audio_data, axis=1)
                
                combined_audio.append(audio_data)
            
            if not combined_audio:
                self.logger.error("No valid audio files to combine")
                return False
                
            # Concatenate all audio
            final_audio = np.concatenate(combined_audio)
            
            # Write combined audio to output file
            sf.write(output_path, final_audio, sample_rate)
            return True
            
        except Exception as e:
            logger.error(f"Error combining audio files: {str(e)}")
            return False

    def create_silence_audio(self, duration_seconds: int, output_path: str) -> bool:
        """Create a silence audio file for the specified duration."""
        try:
            # Create silence array
            silence_samples = int(self.sample_rate * duration_seconds)
            silence = np.zeros(silence_samples, dtype=np.float32)
            
            # Write silence to file
            sf.write(output_path, silence, self.sample_rate)
            return True
        except Exception as e:
            logger.error(f"Error creating silence audio: {str(e)}")
            return False

    def get_audio_duration(self, audio_path: str) -> float:
        """Get the duration of an audio file in seconds."""
        try:
            if not os.path.exists(audio_path):
                return 0.0
            audio_data, sample_rate = sf.read(audio_path)
            return len(audio_data) / sample_rate
        except Exception as e:
            logger.error(f"Error getting audio duration: {str(e)}")
            return 0.0

    def generate_audio_from_text(self, text: str, output_path: str) -> bool:
        """Generate audio from text with proper chunking."""
        try:
            if not text.strip():
                logger.error("Empty text provided for audio generation")
                return False

            # For shorter texts, generate directly
            if len(text) <= self.max_chars_per_chunk:
                return self.text_to_speech_chunk(text, output_path)
            
            # For longer texts, split into chunks
            chunks = self.split_text_into_chunks(text)
            if not chunks:
                logger.error("No valid text chunks found")
                return False
            
            # Generate audio for each chunk
            chunk_files = []
            for i, chunk_text in enumerate(chunks):
                chunk_file = f"{output_path}_chunk_{i:03d}.mp3"
                if self.text_to_speech_chunk(chunk_text, chunk_file):
                    chunk_files.append(chunk_file)
                else:
                    # Clean up on failure
                    for cf in chunk_files:
                        try:
                            os.remove(cf)
                        except:
                            pass
                    return False
            
            # Combine chunks
            success = self.combine_audio_files(chunk_files, output_path)
            
            # Clean up chunk files
            for chunk_file in chunk_files:
                try:
                    os.remove(chunk_file)
                except:
                    pass
            
            return success
            
        except Exception as e:
            logger.error(f"Error in generate_audio_from_text: {str(e)}")
            return False

    def split_text_into_chunks(self, text: str) -> List[str]:
        """Split text into chunks that respect sentence boundaries."""
        # Clean up the text first
        text = re.sub(r'\s+', ' ', text.strip())
        
        # Split by paragraphs first
        paragraphs = text.split('\n\n')
        chunks = []
        current_chunk = ""
        
        for paragraph in paragraphs:
            # If adding this paragraph would exceed the limit, save current chunk
            if len(current_chunk) + len(paragraph) + 2 > self.max_chars_per_chunk and current_chunk:
                chunks.append(current_chunk.strip())
                current_chunk = ""
            
            # Add paragraph to current chunk
            if current_chunk:
                current_chunk += "\n\n" + paragraph
            else:
                current_chunk = paragraph
                
            # If a single paragraph is too long, split by sentences
            if len(current_chunk) > self.max_chars_per_chunk:
                sentences = re.split(r'(?<=[.!?])\s+', current_chunk)
                current_chunk = ""
                for sentence in sentences:
                    if len(current_chunk) + len(sentence) + 1 > self.max_chars_per_chunk and current_chunk:
                        chunks.append(current_chunk.strip())
                        current_chunk = ""
                    
                    if current_chunk:
                        current_chunk += " " + sentence
                    else:
                        current_chunk = sentence
        
        # Add the last chunk
        if current_chunk.strip():
            chunks.append(current_chunk.strip())
            
        return [chunk for chunk in chunks if len(chunk.strip()) > 10]

    def generate_lesson_audio_with_30s_gaps(self, script_text: str, lesson_id: str, voice: str = "alloy") -> Dict:
        """
        Generate lesson audio with exactly 30-second gaps between sections.
        Reads everything in the lecture script but adds gaps between natural sections.
        """
        
        try:
            self.logger.info(f"Generating lesson audio with 30s gaps for lesson {lesson_id}")
            
            # Set voice
            self.set_voice(voice)
            
            # Split script into natural sections
            sections = self.split_script_into_natural_sections(script_text)
            
            if not sections:
                return {"success": False, "error": "No sections found in script"}
            
            self.logger.info(f"Processing {len(sections)} sections")
            
            # Create temporary directory for this lesson
            temp_lesson_dir = self.temp_dir / f"lesson_{lesson_id}_{int(time.time())}"
            temp_lesson_dir.mkdir(exist_ok=True)
            
            audio_parts = []
            total_speech_duration = 0
            
            for i, section in enumerate(sections):
                try:
                    section_content = section['content'].strip()
                    section_title = section['title']
                    
                    if not section_content:
                        self.logger.warning(f"Empty content for section: {section_title}")
                        continue
                    
                    self.logger.info(f"Generating audio for section {i+1}: '{section_title}' ({len(section_content)} chars)")
                    self.logger.info(f"Section content preview: {section_content[:200]}...")
                    
                    # Generate audio for this section
                    section_audio_path = temp_lesson_dir / f"section_{i:02d}.mp3"
                    
                    success = self.generate_audio_from_text(
                        text=section_content,
                        output_path=str(section_audio_path)
                    )
                    
                    if not success or not section_audio_path.exists():
                        self.logger.error(f"Failed to generate audio for section: {section_title}")
                        continue
                    
                    # Add section audio to the list
                    audio_parts.append(str(section_audio_path))
                    
                    # Track speech duration
                    section_duration = self.get_audio_duration(str(section_audio_path))
                    total_speech_duration += section_duration
                    
                    self.logger.info(f"Generated audio for '{section_title}': {section_duration:.1f}s")
                    
                    # Add 30-second gap after each section except the last one
                    if i < len(sections) - 1:
                        gap_audio_path = temp_lesson_dir / f"gap_{i:02d}.mp3"
                        if self.create_silence_audio(30, str(gap_audio_path)):
                            audio_parts.append(str(gap_audio_path))
                            self.logger.info(f"Added 30-second gap after section {i+1}")
                        else:
                            self.logger.warning(f"Failed to create gap audio after section {i+1}")
                    
                except Exception as section_error:
                    self.logger.error(f"Error processing section {section['title']}: {section_error}")
                    continue
            
            if not audio_parts:
                return {"success": False, "error": "No audio segments generated"}
            
            # Combine all audio parts into one file
            combined_audio_path = temp_lesson_dir / f"{lesson_id}_combined.mp3"
            
            self.logger.info(f"Combining {len(audio_parts)} audio parts into final file")
            combine_success = self.combine_audio_files(audio_parts, str(combined_audio_path))
            
            if not combine_success or not combined_audio_path.exists():
                return {"success": False, "error": "Failed to combine audio segments"}
            
            # Get final audio duration
            final_duration_seconds = self.get_audio_duration(str(combined_audio_path))
            final_duration_minutes = final_duration_seconds / 60.0
            
            # Calculate total gap time
            total_gaps = len(sections) - 1
            total_gap_seconds = total_gaps * 30
            
            result = {
                "success": True,
                "audio_file": str(combined_audio_path),
                "sections_count": len(sections),
                "total_duration_minutes": round(final_duration_minutes, 2),
                "speech_duration_seconds": round(total_speech_duration, 2),
                "gap_duration_seconds": total_gap_seconds,
                "gaps_added": total_gaps
            }
            
            self.logger.info(f"Final audio: {final_duration_minutes:.1f} min total "
                            f"({total_speech_duration/60:.1f} min speech + {total_gap_seconds/60:.1f} min gaps)")
            
            return result
            
        except Exception as e:
            self.logger.error(f"Error in generate_lesson_audio_with_30s_gaps: {str(e)}")
            return {"success": False, "error": str(e)}

    def extract_script_text_from_pdf_url(self, pdf_url: str) -> Optional[str]:
        """Extract text from a PDF URL (for prepared lesson scripts)."""
        try:
            logger.info(f"Downloading script PDF from: {pdf_url}")
            
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
            }
            
            response = requests.get(pdf_url, headers=headers, timeout=30)
            response.raise_for_status()
            
            if not response.content.startswith(b'%PDF'):
                logger.error("Downloaded content is not a valid PDF")
                return None
            
            # Use PyPDF2 to extract text
            pdf_file = io.BytesIO(response.content)
            pdf_reader = PyPDF2.PdfReader(pdf_file)
            
            text = ""
            for page in pdf_reader.pages:
                try:
                    page_text = page.extract_text()
                    if page_text:
                        text += page_text + "\n"
                except Exception as e:
                    logger.warning(f"Could not extract text from page: {e}")
            
            return text.strip() if text.strip() else None
            
        except Exception as e:
            logger.error(f"Error extracting script text from PDF: {e}")
            return None

    def generate_timed_lesson_audio(self, teacher_id: str, course_id: str, lesson_id: str, 
                                  lesson_title: str, script_url: str, date: str,
                                  voice: str = "alloy") -> Dict:
        """
        Generate lesson audio with 30-second gaps and upload to Supabase.
        """
        result = {
            'success': False,
            'lesson_id': lesson_id,
            'audio_url': None,
            'error': None
        }
        
        try:
            # Extract script text from PDF
            self.logger.info(f"Extracting script text for lesson {lesson_id}")
            script_text = self.extract_script_text_from_pdf_url(script_url)
            
            if not script_text:
                result['error'] = "Failed to extract script text from PDF"
                return result
            
            self.logger.info(f"Extracted {len(script_text)} characters from script")
            
            # Generate audio with 30-second gaps
            audio_result = self.generate_lesson_audio_with_30s_gaps(
                script_text=script_text,
                lesson_id=lesson_id,
                voice=voice
            )
            
            if not audio_result['success']:
                result['error'] = audio_result.get('error', 'Failed to generate audio')
                return result
            
            combined_audio_path = audio_result['audio_file']
            
            # Upload to Supabase
            try:
                client = SupabaseClient(teacher_id=teacher_id)
                
                audio_filename = f"{lesson_id}_complete_audio.mp3"
                bucket_path = f"{teacher_id}/{course_id}/{date}/{audio_filename}"
                
                with open(combined_audio_path, 'rb') as f:
                    audio_bytes = f.read()
                
                # Upload to bucket
                client.upload_pdf_to_bucket(
                    bucket=self.audio_bucket,
                    pdf_bytes=audio_bytes,
                    path=bucket_path,
                    upsert=True
                )
                
                # Get URL
                sign_urls = os.getenv("SIGN_URLS", "true").lower() == "true"
                if sign_urls:
                    audio_url = client.create_signed_url(
                        self.audio_bucket, bucket_path, expires_in=86400
                    )
                else:
                    audio_url = client.get_public_url(self.audio_bucket, bucket_path)
                
                # Update database
                try:
                    from supabase import create_client
                    url = os.getenv("SUPABASE_URL")
                    key = os.getenv("SUPABASE_KEY")
                    supabase = create_client(url, key)
                    
                    update_result = supabase.table('prepared_lessons').update({
                        'audio_url': audio_url
                    }).eq('lesson_id', lesson_id).eq('teacher_id', teacher_id).execute()
                    
                    self.logger.info(f"Updated prepared_lessons with audio URL for lesson {lesson_id}")
                    
                except Exception as db_error:
                    self.logger.warning(f"Failed to update prepared_lessons table: {db_error}")
                
                result['success'] = True
                result['audio_url'] = audio_url
                result['duration_minutes'] = audio_result['total_duration_minutes']
                result['sections_count'] = audio_result['sections_count']
                result['speech_duration_seconds'] = audio_result['speech_duration_seconds']
                result['gap_duration_seconds'] = audio_result['gap_duration_seconds']
                result['gaps_added'] = audio_result['gaps_added']
                result['bucket_path'] = bucket_path
                
                self.logger.info(f"Successfully uploaded audio for lesson {lesson_id} "
                               f"({audio_result['total_duration_minutes']:.1f} min total, "
                               f"{audio_result['gaps_added']} x 30s gaps)")
                
            except Exception as upload_error:
                self.logger.error(f"Failed to upload audio to Supabase: {upload_error}")
                result['error'] = f"Upload failed: {str(upload_error)}"
            
            # Clean up temporary files
            try:
                temp_dir = Path(combined_audio_path).parent
                for file_path in temp_dir.glob("*"):
                    file_path.unlink(missing_ok=True)
                temp_dir.rmdir()
            except Exception as cleanup_error:
                self.logger.warning(f"Failed to clean up temporary files: {cleanup_error}")
            
            return result
            
        except Exception as e:
            self.logger.error(f"Error in generate_timed_lesson_audio: {str(e)}")
            result['error'] = str(e)
            return result