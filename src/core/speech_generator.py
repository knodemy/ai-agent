from openai import OpenAI
import os
import hashlib
from pathlib import Path
import logging
from datetime import datetime
import re

class SpeechGenerator:
    def __init__(self):
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key or api_key == "your_openai_api_key":
            raise ValueError("Please set your OPENAI_API_KEY in .env file")
        self.openai_client = OpenAI(api_key=api_key)
        self.cache_dir = Path("temp/audio_cache")
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.logger = logging.getLogger(__name__)
        
        # OpenAI TTS has a 4096 character limit
        self.max_chunk_size = 4000  # Leave some buffer

    def split_text_into_chunks(self, text: str) -> list:
        """Split text into chunks that fit within OpenAI TTS character limit"""
        if len(text) <= self.max_chunk_size:
            return [text]
        
        chunks = []
        current_chunk = ""
        
        # Split by timing markers first (like [2:00], [4:00], etc.)
        timing_pattern = r'\[(\d+):(\d+)\]'
        sections = re.split(timing_pattern, text)
        
        # Recombine with timing markers
        reconstructed_sections = []
        i = 0
        while i < len(sections):
            if i == 0:
                # First section (before any timing marker)
                if sections[i].strip():
                    reconstructed_sections.append(sections[i])
            elif i + 2 < len(sections):
                # Found timing marker pattern
                timing_marker = f"[{sections[i]}:{sections[i+1]}]"
                content = sections[i+2]
                reconstructed_sections.append(timing_marker + content)
                i += 2
            i += 1
        
        # If no timing markers found, split by sentences
        if len(reconstructed_sections) <= 1:
            sentences = re.split(r'(?<=[.!?])\s+', text)
            reconstructed_sections = sentences
        
        # Group sections into chunks under the character limit
        for section in reconstructed_sections:
            if len(current_chunk + section) <= self.max_chunk_size:
                current_chunk += section + " "
            else:
                if current_chunk:
                    chunks.append(current_chunk.strip())
                current_chunk = section + " "
        
        if current_chunk:
            chunks.append(current_chunk.strip())
        
        # If any individual chunk is still too long, split it further
        final_chunks = []
        for chunk in chunks:
            if len(chunk) <= self.max_chunk_size:
                final_chunks.append(chunk)
            else:
                # Split by sentences as last resort
                sentences = re.split(r'(?<=[.!?])\s+', chunk)
                temp_chunk = ""
                for sentence in sentences:
                    if len(temp_chunk + sentence) <= self.max_chunk_size:
                        temp_chunk += sentence + " "
                    else:
                        if temp_chunk:
                            final_chunks.append(temp_chunk.strip())
                        temp_chunk = sentence + " "
                if temp_chunk:
                    final_chunks.append(temp_chunk.strip())
        
        return final_chunks

    def text_to_speech(self, text_content: str, lesson_title: str = None) -> dict:
        """Convert text to speech using OpenAI TTS with chunking support"""
        try:
            # Create cache key
            cache_key = hashlib.md5(text_content.encode()).hexdigest()
            if lesson_title:
                filename = f"{lesson_title.replace(' ', '_').replace('/', '_')}_{cache_key[:8]}.mp3"
            else:
                filename = f"{cache_key}.mp3"
            
            cache_file = self.cache_dir / filename
            
            # Check if already cached
            if cache_file.exists():
                self.logger.info(f"Using cached audio: {cache_file}")
                file_size = cache_file.stat().st_size / 1024 / 1024  # MB
                return {
                    'audio_file': str(cache_file),
                    'cached': True,
                    'file_size_mb': round(file_size, 2)
                }
            
            # Check if text needs to be chunked
            char_count = len(text_content)
            self.logger.info(f"Processing text with {char_count} characters")
            
            if char_count > self.max_chunk_size:
                return self._generate_chunked_audio(text_content, lesson_title, cache_file)
            else:
                return self._generate_single_audio(text_content, cache_file, char_count)
            
        except Exception as e:
            self.logger.error(f"Error generating speech: {e}")
            raise Exception(f"Failed to generate speech: {str(e)}")

    def _generate_single_audio(self, text_content: str, cache_file: Path, char_count: int) -> dict:
        """Generate audio for text that fits in single request"""
        self.logger.info("Generating speech audio with OpenAI TTS (single chunk)...")
        
        # Estimate cost (approximately $15 per 1M characters)
        estimated_cost = (char_count / 1000000) * 15
        self.logger.info(f"Estimated cost: ${estimated_cost:.4f} ({char_count} characters)")
        
        # Generate speech
        response = self.openai_client.audio.speech.create(
            model="tts-1-hd",  # High quality model
            voice="alloy",     # Professional voice options: alloy, echo, fable, onyx, nova, shimmer
            input=text_content,
            speed=1.0
        )
        
        # Save to cache
        with open(cache_file, 'wb') as f:
            f.write(response.content)
        
        file_size = cache_file.stat().st_size / 1024 / 1024  # MB
        self.logger.info(f"Audio generated and saved: {cache_file} ({file_size:.2f} MB)")
        
        return {
            'audio_file': str(cache_file),
            'cached': False,
            'file_size_mb': round(file_size, 2),
            'estimated_cost': round(estimated_cost, 4),
            'character_count': char_count
        }

    def _generate_chunked_audio(self, text_content: str, lesson_title: str, cache_file: Path) -> dict:
        """Generate audio for long text by chunking it"""
        chunks = self.split_text_into_chunks(text_content)
        total_char_count = len(text_content)
        
        self.logger.info(f"Text too long ({total_char_count} chars), splitting into {len(chunks)} chunks")
        
        # Generate audio for the first chunk only and save as main file
        # This provides immediate playback while being cost-effective
        first_chunk = chunks[0]
        self.logger.info(f"Generating audio for first chunk ({len(first_chunk)} characters)")
        
        # Estimate cost for first chunk only
        estimated_cost = (len(first_chunk) / 1000000) * 15
        
        # Generate speech for first chunk
        response = self.openai_client.audio.speech.create(
            model="tts-1-hd",
            voice="alloy",
            input=first_chunk,
            speed=1.0
        )
        
        # Save first chunk as main audio file
        with open(cache_file, 'wb') as f:
            f.write(response.content)
        
        file_size = cache_file.stat().st_size / 1024 / 1024  # MB
        self.logger.info(f"First chunk audio saved: {cache_file} ({file_size:.2f} MB)")
        
        return {
            'audio_file': str(cache_file),
            'cached': False,
            'file_size_mb': round(file_size, 2),
            'estimated_cost': round(estimated_cost, 4),
            'character_count': len(first_chunk),
            'total_chunks': len(chunks),
            'chunk_info': f"Playing first chunk ({len(first_chunk)} of {total_char_count} chars)"
        }

    def generate_full_chunked_audio(self, text_content: str, lesson_title: str) -> dict:
        """Generate complete audio for all chunks (use this for full lecture generation)"""
        try:
            chunks = self.split_text_into_chunks(text_content)
            total_char_count = len(text_content)
            
            self.logger.info(f"Generating complete audio: {len(chunks)} chunks, {total_char_count} total characters")
            
            audio_files = []
            total_cost = 0
            total_size = 0
            
            for i, chunk in enumerate(chunks):
                chunk_filename = f"{lesson_title.replace(' ', '_').replace('/', '_')}_chunk_{i+1}.mp3"
                chunk_file = self.cache_dir / chunk_filename
                
                # Generate audio for this chunk
                chunk_cost = (len(chunk) / 1000000) * 15
                total_cost += chunk_cost
                
                response = self.openai_client.audio.speech.create(
                    model="tts-1-hd",
                    voice="alloy",
                    input=chunk,
                    speed=1.0
                )
                
                with open(chunk_file, 'wb') as f:
                    f.write(response.content)
                
                chunk_size = chunk_file.stat().st_size / 1024 / 1024
                total_size += chunk_size
                
                audio_files.append({
                    'file': str(chunk_file),
                    'chunk_number': i + 1,
                    'size_mb': round(chunk_size, 2),
                    'character_count': len(chunk)
                })
                
                self.logger.info(f"Generated chunk {i+1}/{len(chunks)}: {chunk_filename}")
            
            return {
                'audio_files': audio_files,
                'total_chunks': len(chunks),
                'total_size_mb': round(total_size, 2),
                'total_cost': round(total_cost, 4),
                'total_characters': total_char_count
            }
            
        except Exception as e:
            self.logger.error(f"Error generating full chunked audio: {e}")
            return {'error': str(e)}

    def generate_speech_segments(self, script: str, lesson_title: str) -> dict:
        """Generate speech for different segments of the lecture"""
        try:
            # Split script into segments (by timing markers or sections)
            segments = self._split_script_into_segments(script)
            
            audio_files = []
            total_size = 0
            
            for i, segment in enumerate(segments):
                if segment.strip():
                    segment_title = f"{lesson_title}_segment_{i+1}"
                    result = self.text_to_speech(segment, segment_title)
                    audio_files.append(result)
                    total_size += result['file_size_mb']
            
            return {
                'segment_files': audio_files,
                'total_segments': len(audio_files),
                'total_size_mb': round(total_size, 2)
            }
            
        except Exception as e:
            self.logger.error(f"Error generating speech segments: {e}")
            return {'error': str(e)}

    def _split_script_into_segments(self, script: str) -> list:
        """Split script into logical segments"""
        # Simple split by double newlines or timing markers
        segments = []
        current_segment = ""
        
        lines = script.split('\n')
        for line in lines:
            if '[' in line and ']' in line and any(x in line for x in ['00', '10', '20', '30']):
                # Timing marker found, start new segment
                if current_segment.strip():
                    segments.append(current_segment.strip())
                current_segment = line + '\n'
            else:
                current_segment += line + '\n'
        
        if current_segment.strip():
            segments.append(current_segment.strip())
        
        return segments