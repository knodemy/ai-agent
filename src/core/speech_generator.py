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

    def clean_script_metadata(self, script_text: str) -> str:
        """Remove metadata lines from the beginning of the script and clean formatting."""
        lines = script_text.split('\n')
        cleaned_lines = []
        skip_lines = True
        
        for i, line in enumerate(lines):
            line_stripped = line.strip()
            
            # Skip metadata lines at the beginning (first 4-6 lines typically)
            if skip_lines and i < 10:  # Check first 10 lines for metadata
                # Skip lines that contain metadata patterns
                if (line_stripped.startswith('Lecture Script:') or 
                    line_stripped.startswith('Generated for:') or 
                    line_stripped.startswith('Source:') or 
                    line_stripped.startswith('Generated:') or
                    line_stripped.startswith('---') or
                    'Generated:' in line_stripped or
                    'Source:' in line_stripped or
                    line_stripped == ''):
                    continue
                else:
                    skip_lines = False
            
            # Only add non-metadata lines
            if not skip_lines:
                cleaned_lines.append(line)
        
        return '\n'.join(cleaned_lines).strip()

    def clean_segment_content_for_tts(self, content: str) -> str:
        """Clean segment content specifically for TTS - remove all non-speech elements."""
        
        # Remove timing markers that might be embedded in content
        content = re.sub(r'\[\d+:\d+\]', '', content)
        content = re.sub(r'\[(\d+)[-–](\d+)\s*minutes?\]', '', content)
        
        # Remove ALL speaker notes and teaching instructions in brackets
        content = re.sub(r'\[.*?\]', '', content, flags=re.DOTALL)
        
        # Remove markdown formatting
        content = re.sub(r'\*\*(.*?)\*\*', r'\1', content)  # Remove bold markers
        content = re.sub(r'\*(.*?)\*', r'\1', content)      # Remove italic markers
        content = re.sub(r'#{1,6}\s*', '', content)         # Remove heading markers
        
        # Convert bullet points to natural speech
        content = re.sub(r'^\s*[-•]\s*', '', content, flags=re.MULTILINE)
        
        # Remove numbered lists formatting
        content = re.sub(r'^\s*\d+\.\s*', '', content, flags=re.MULTILINE)
        
        # Clean up multiple spaces and newlines
        content = re.sub(r'\n\s*\n\s*\n+', '\n\n', content)
        content = re.sub(r' {2,}', ' ', content)
        content = re.sub(r'\t', ' ', content)
        
        # Remove any remaining metadata-like lines
        lines = content.split('\n')
        cleaned_lines = []
        for line in lines:
            line_stripped = line.strip()
            # Skip obvious metadata or instruction lines
            if (not line_stripped or
                line_stripped.startswith('Note:') or
                line_stripped.startswith('Instruction:') or
                line_stripped.startswith('Teacher:') or
                'visual aid' in line_stripped.lower() or
                'show slide' in line_stripped.lower()):
                continue
            cleaned_lines.append(line)
        
        result = '\n'.join(cleaned_lines).strip()
        
        # Final check - ensure we have actual content
        if len(result.replace(' ', '').replace('\n', '')) < 50:
            self.logger.warning("Content very short after cleaning, may be over-cleaned")
        
        return result

    def parse_lecture_segments(self, script_text: str) -> List[LectureSegment]:
        """Parse the script into timed segments based on timing markers."""
        
        # Clean the script first - remove metadata
        clean_script = self.clean_script_metadata(script_text)
        
        segments = []
        current_segment = None
        current_content = []
        segment_order = 0
        
        lines = clean_script.split('\n')
        
        for line in lines:
            line = line.strip()
            if not line:
                if current_content:
                    current_content.append('')
                continue
            
            # Look for timing markers like [Opening Hook: 3-5 minutes] or [3-5 minutes]
            timing_match = re.search(r'\[([^:]*?):\s*(\d+)[-–](\d+)\s*minutes?\]', line)
            simple_timing_match = re.search(r'\[(\d+)[-–](\d+)\s*minutes?\]', line)
            
            if timing_match:
                # Save previous segment if exists
                if current_segment and current_content:
                    current_segment.content = '\n'.join(current_content).strip()
                    segments.append(current_segment)
                
                # Create new segment
                title = timing_match.group(1).strip()
                duration_min = int(timing_match.group(2))
                duration_max = int(timing_match.group(3))
                
                # Determine segment type
                segment_type = self._determine_segment_type(title)
                
                current_segment = LectureSegment(
                    title=title,
                    content="",
                    duration_min=duration_min,
                    duration_max=duration_max,
                    order=segment_order,
                    segment_type=segment_type
                )
                segment_order += 1
                current_content = []
                
            elif simple_timing_match and current_segment:
                # Update duration if we find a simple timing marker
                current_segment.duration_min = int(simple_timing_match.group(1))
                current_segment.duration_max = int(simple_timing_match.group(2))
                
            else:
                # Add content to current segment (skip the timing marker line itself)
                if not re.search(r'\[.*?minutes?\]', line):
                    current_content.append(line)
        
        # Add the last segment
        if current_segment and current_content:
            current_segment.content = '\n'.join(current_content).strip()
            segments.append(current_segment)
        
        return segments

    def _determine_segment_type(self, title: str) -> str:
        """Determine the type of segment based on title."""
        title_lower = title.lower()
        
        if 'hook' in title_lower or 'opening' in title_lower or 'introduction' in title_lower:
            return 'hook'
        elif 'objective' in title_lower or 'learning' in title_lower or 'goals' in title_lower:
            return 'objectives'
        elif 'practice' in title_lower or 'application' in title_lower or 'activity' in title_lower:
            return 'practice'
        elif 'recap' in title_lower or 'takeaway' in title_lower or 'conclusion' in title_lower or 'summary' in title_lower:
            return 'recap'
        else:
            return 'content'

    def text_to_speech_chunk(self, text: str, output_path: str) -> bool:
        """Convert a single text chunk to speech using OpenAI's TTS API."""
        try:
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
                # Read audio file
                audio_data, sr = sf.read(audio_file)
                
                if sample_rate is None:
                    sample_rate = sr
                elif sr != sample_rate:
                    # Resample if needed (basic implementation)
                    self.logger.warning(f"Sample rate mismatch: {sr} vs {sample_rate}")
                
                # Ensure audio is 1D (mono)
                if len(audio_data.shape) > 1:
                    audio_data = np.mean(audio_data, axis=1)
                
                combined_audio.append(audio_data)
            
            if not combined_audio:
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
            audio_data, sample_rate = sf.read(audio_path)
            return len(audio_data) / sample_rate
        except Exception as e:
            logger.error(f"Error getting audio duration: {str(e)}")
            return 0.0

    def extend_audio_with_silence(self, audio_path: str, target_duration_seconds: float, output_path: str) -> bool:
        """Extend an audio file with silence to reach target duration."""
        try:
            # Read original audio
            audio_data, sample_rate = sf.read(audio_path)
            current_duration = len(audio_data) / sample_rate
            
            if current_duration >= target_duration_seconds:
                # Already long enough, just copy
                sf.write(output_path, audio_data, sample_rate)
                return True
            
            # Calculate silence needed
            silence_duration = target_duration_seconds - current_duration
            silence_samples = int(sample_rate * silence_duration)
            silence = np.zeros(silence_samples, dtype=audio_data.dtype)
            
            # Combine audio and silence
            extended_audio = np.concatenate([audio_data, silence])
            
            # Write extended audio
            sf.write(output_path, extended_audio, sample_rate)
            return True
            
        except Exception as e:
            logger.error(f"Error extending audio with silence: {str(e)}")
            return False

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
            
            # Combine chunks using soundfile
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

    def generate_timed_audio_segments_with_pauses(self, script_text: str, output_dir: str, 
                                                lesson_id: str, voice: str = "alloy") -> Dict:
        """
        Generate separate audio files for each lecture segment with proper timing and pauses.
        Each segment will be padded to match its minimum duration with silence.
        """
        
        try:
            output_path = Path(output_dir)
            output_path.mkdir(parents=True, exist_ok=True)
            
            # Parse script into segments
            segments = self.parse_lecture_segments(script_text)
            
            if not segments:
                return {"success": False, "error": "No segments found in script"}
            
            self.logger.info(f"Found {len(segments)} lecture segments")
            
            # Set voice
            self.set_voice(voice)
            
            segment_files = []
            playlist_data = {
                "lesson_id": lesson_id,
                "total_segments": len(segments),
                "segments": [],
                "total_duration_min": sum(s.duration_min for s in segments),
                "total_duration_max": sum(s.duration_max for s in segments)
            }
            
            for segment in segments:
                try:
                    # Clean the content specifically for TTS
                    clean_content = self.clean_segment_content_for_tts(segment.content)
                    
                    if not clean_content.strip():
                        self.logger.warning(f"Empty content for segment: {segment.title}")
                        continue
                    
                    # Log what we're about to convert to speech
                    self.logger.info(f"TTS Content for '{segment.title}' ({len(clean_content)} chars): {clean_content[:100]}...")
                    
                    # Generate filename for speech audio
                    safe_title = re.sub(r'[^\w\s-]', '', segment.title)
                    safe_title = re.sub(r'[-\s]+', '_', safe_title)
                    speech_filename = f"{lesson_id}_segment_{segment.order:02d}_{safe_title}_speech.mp3"
                    speech_path = output_path / speech_filename
                    
                    # Generate audio for this segment's content
                    self.logger.info(f"Generating speech audio for segment: {segment.title}")
                    
                    success = self.generate_audio_from_text(
                        text=clean_content,
                        output_path=str(speech_path)
                    )
                    
                    if not success or not speech_path.exists():
                        self.logger.error(f"Failed to generate speech for segment: {segment.title}")
                        continue
                    
                    # Get the duration of the generated speech audio
                    speech_duration_seconds = self.get_audio_duration(str(speech_path))
                    speech_duration_minutes = speech_duration_seconds / 60.0
                    
                    self.logger.info(f"Speech duration for '{segment.title}': {speech_duration_minutes:.1f} minutes")
                    
                    # Calculate if we need to add silence to meet minimum duration
                    min_duration_seconds = segment.duration_min * 60
                    final_filename = f"{lesson_id}_segment_{segment.order:02d}_{safe_title}.mp3"
                    final_path = output_path / final_filename
                    
                    if speech_duration_seconds < min_duration_seconds:
                        # Extend with silence to meet minimum duration
                        success = self.extend_audio_with_silence(
                            str(speech_path), 
                            min_duration_seconds, 
                            str(final_path)
                        )
                        silence_added_seconds = min_duration_seconds - speech_duration_seconds
                        self.logger.info(f"Added {silence_added_seconds:.1f} seconds of silence to '{segment.title}'")
                    else:
                        # Just copy the speech file
                        sf.copy(str(speech_path), str(final_path))
                        silence_added_seconds = 0
                    
                    # Remove the temporary speech-only file
                    speech_path.unlink()
                    
                    final_duration_seconds = self.get_audio_duration(str(final_path))
                    final_duration_minutes = final_duration_seconds / 60.0
                    
                    segment_info = {
                        "order": segment.order,
                        "title": segment.title,
                        "type": segment.segment_type,
                        "duration_min": segment.duration_min,
                        "duration_max": segment.duration_max,
                        "actual_duration_minutes": round(final_duration_minutes, 2),
                        "speech_duration_minutes": round(speech_duration_minutes, 2),
                        "silence_added_seconds": round(silence_added_seconds, 2),
                        "audio_file": final_filename,
                        "content_length": len(clean_content),
                        "cleaned_content_preview": clean_content[:200] + "..." if len(clean_content) > 200 else clean_content
                    }
                    
                    playlist_data["segments"].append(segment_info)
                    segment_files.append(str(final_path))
                    
                    self.logger.info(f"Generated timed audio segment: {final_filename} ({final_duration_minutes:.1f} min)")
                    
                except Exception as segment_error:
                    self.logger.error(f"Error processing segment {segment.title}: {segment_error}")
                    continue
            
            # Save playlist/metadata file
            playlist_file = output_path / f"{lesson_id}_playlist.json"
            with open(playlist_file, 'w', encoding='utf-8') as f:
                json.dump(playlist_data, f, indent=2, ensure_ascii=False)
            
            return {
                "success": True,
                "segments_generated": len(segment_files),
                "segment_files": segment_files,
                "playlist_file": str(playlist_file),
                "playlist_data": playlist_data,
                "total_duration_minutes": playlist_data.get("total_duration_min", 0)
            }
            
        except Exception as e:
            self.logger.error(f"Error in generate_timed_audio_segments_with_pauses: {str(e)}")
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
        Generate combined audio for a lesson script and upload to Supabase.
        Creates a single audio file from all segments instead of individual segments.
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
            
            # Create temporary directory for segments
            temp_segment_dir = self.temp_dir / f"segments_{lesson_id}_{int(time.time())}"
            temp_segment_dir.mkdir(exist_ok=True)
            
            # Generate timed audio segments with pauses
            segment_result = self.generate_timed_audio_segments_with_pauses(
                script_text=script_text,
                output_dir=str(temp_segment_dir),
                lesson_id=lesson_id,
                voice=voice
            )
            
            if not segment_result['success']:
                result['error'] = segment_result.get('error', 'Failed to generate segments')
                return result
            
            # Combine all segments into one audio file
            combined_audio_path = temp_segment_dir / f"{lesson_id}_complete_audio.mp3"
            
            self.logger.info(f"Combining {len(segment_result['segment_files'])} segments into single audio file")
            
            combine_success = self.combine_audio_files(
                segment_result['segment_files'],
                str(combined_audio_path)
            )
            
            if not combine_success or not combined_audio_path.exists():
                result['error'] = "Failed to combine audio segments"
                return result
            
            # Upload only the combined audio file to Supabase
            try:
                client = SupabaseClient(teacher_id=teacher_id)
                
                # Upload combined audio
                audio_filename = f"{lesson_id}_complete_audio.mp3"
                bucket_path = f"{teacher_id}/{course_id}/{date}/{audio_filename}"
                
                with open(combined_audio_path, 'rb') as f:
                    audio_bytes = f.read()
                
                # Upload to bucket
                client.upload_pdf_to_bucket(  # Reuse for audio files
                    bucket=self.audio_bucket,
                    pdf_bytes=audio_bytes,
                    path=bucket_path,
                    upsert=True
                )
                
                # Get URL
                sign_urls = os.getenv("SIGN_URLS", "true").lower() == "true"
                if sign_urls:
                    audio_url = client.create_signed_url(
                        self.audio_bucket, bucket_path, expires_in=86400  # 24 hours
                    )
                else:
                    audio_url = client.get_public_url(self.audio_bucket, bucket_path)
                
                # Update the prepared_lessons table with audio URL
                try:
                    # Get the prepared lesson record
                    from supabase import create_client
                    url = os.getenv("SUPABASE_URL")
                    key = os.getenv("SUPABASE_KEY")
                    supabase = create_client(url, key)
                    
                    # Update the audio_url field
                    update_result = supabase.table('prepared_lessons').update({
                        'audio_url': audio_url
                    }).eq('lesson_id', lesson_id).eq('teacher_id', teacher_id).execute()
                    
                    self.logger.info(f"Updated prepared_lessons with audio URL for lesson {lesson_id}")
                    
                except Exception as db_error:
                    self.logger.warning(f"Failed to update prepared_lessons table: {db_error}")
                
                # Get audio duration for result
                audio_duration_seconds = self.get_audio_duration(str(combined_audio_path))
                audio_duration_minutes = audio_duration_seconds / 60.0
                
                result['success'] = True
                result['audio_url'] = audio_url
                result['duration_minutes'] = round(audio_duration_minutes, 2)
                result['segments_count'] = len(segment_result['segment_files'])
                result['bucket_path'] = bucket_path
                
                self.logger.info(f"Successfully uploaded combined audio for lesson {lesson_id} ({audio_duration_minutes:.1f} min)")
                
            except Exception as upload_error:
                self.logger.error(f"Failed to upload combined audio to Supabase: {upload_error}")
                result['error'] = f"Upload failed: {str(upload_error)}"
            
            # Clean up temporary files
            try:
                for segment_file in segment_result['segment_files']:
                    Path(segment_file).unlink(missing_ok=True)
                Path(segment_result['playlist_file']).unlink(missing_ok=True)
                combined_audio_path.unlink(missing_ok=True)
                temp_segment_dir.rmdir()
            except Exception as cleanup_error:
                self.logger.warning(f"Failed to clean up temporary files: {cleanup_error}")
            
            return result
            
        except Exception as e:
            self.logger.error(f"Error in generate_timed_lesson_audio: {str(e)}")
            result['error'] = str(e)
            return result