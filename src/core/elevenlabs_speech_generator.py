import os
import hashlib
import requests
import json
from pathlib import Path
import logging
from datetime import datetime
from typing import Dict, List, Optional
import time

class ElevenLabsSpeechGenerator:
    def __init__(self):
        self.api_key = os.getenv("ELEVENLABS_API_KEY")
        if not self.api_key or self.api_key == "your_elevenlabs_api_key":
            raise ValueError("Please set your ELEVENLABS_API_KEY in .env file")
        
        self.base_url = "https://api.elevenlabs.io/v1"
        self.headers = {
            "Accept": "audio/mpeg",
            "Content-Type": "application/json",
            "xi-api-key": self.api_key
        }
        
        self.cache_dir = Path("temp/audio_cache")
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.logger = logging.getLogger(__name__)
        
        # ElevenLabs character limits (varies by subscription)
        self.max_chunk_size = 2500  # Conservative limit for most plans
        
        # Default voice settings for lectures
        self.voice_settings = {
            "stability": 0.75,        # Higher stability for consistent lecture delivery
            "similarity_boost": 0.85, # High similarity to maintain voice consistency
            "style": 0.2,             # Lower style for more neutral delivery
            "use_speaker_boost": True
        }

    def get_available_voices(self) -> List[Dict]:
        """Get all available voices from ElevenLabs"""
        try:
            response = requests.get(f"{self.base_url}/voices", headers=self.headers)
            response.raise_for_status()
            
            voices_data = response.json()
            voices = voices_data.get('voices', [])
            
            # Filter and format voice information
            formatted_voices = []
            for voice in voices:
                formatted_voices.append({
                    'voice_id': voice['voice_id'],
                    'name': voice['name'],
                    'category': voice.get('category', 'Unknown'),
                    'description': voice.get('description', ''),
                    'preview_url': voice.get('preview_url', ''),
                    'labels': voice.get('labels', {}),
                    'settings': voice.get('settings', {})
                })
            
            self.logger.info(f"Found {len(formatted_voices)} available voices")
            return formatted_voices
            
        except Exception as e:
            self.logger.error(f"Error fetching voices: {e}")
            return []

    def get_recommended_lecture_voices(self) -> List[Dict]:
        """Get voices recommended for lectures"""
        all_voices = self.get_available_voices()
        
        # Look for professional, clear voices good for educational content
        lecture_friendly_names = [
            'rachel', 'adam', 'antoni', 'arnold', 'josh', 'sam',
            'bella', 'elli', 'charlotte', 'daniel', 'lily', 'matilda'
        ]
        
        recommended = []
        for voice in all_voices:
            voice_name = voice['name'].lower()
            if any(name in voice_name for name in lecture_friendly_names):
                recommended.append(voice)
        
        # If no recommended voices found, return first few voices
        if not recommended:
            recommended = all_voices[:5]
        
        return recommended

    def get_user_info(self) -> Dict:
        """Get user subscription info and character limits"""
        try:
            response = requests.get(f"{self.base_url}/user", headers=self.headers)
            response.raise_for_status()
            
            user_data = response.json()
            
            # Extract useful information
            subscription = user_data.get('subscription', {})
            character_count = subscription.get('character_count', 0)
            character_limit = subscription.get('character_limit', 10000)
            
            return {
                'character_count': character_count,
                'character_limit': character_limit,
                'characters_remaining': character_limit - character_count,
                'subscription_tier': subscription.get('tier', 'free'),
                'status': subscription.get('status', 'active')
            }
            
        except Exception as e:
            self.logger.error(f"Error fetching user info: {e}")
            return {}

    def split_text_into_chunks(self, text: str) -> List[str]:
        """Split text into chunks that fit within ElevenLabs character limits"""
        if len(text) <= self.max_chunk_size:
            return [text]
        
        chunks = []
        current_chunk = ""
        
        # Split by sentences to maintain natural breaks
        sentences = text.replace('\n', ' ').split('. ')
        
        for sentence in sentences:
            sentence = sentence.strip()
            if not sentence:
                continue
                
            # Add period back if it was removed by split
            if not sentence.endswith('.') and not sentence.endswith('!') and not sentence.endswith('?'):
                sentence += '.'
            
            # Check if adding this sentence would exceed the limit
            if len(current_chunk + ' ' + sentence) <= self.max_chunk_size:
                current_chunk += (' ' + sentence) if current_chunk else sentence
            else:
                # If current chunk is not empty, save it and start new chunk
                if current_chunk:
                    chunks.append(current_chunk.strip())
                current_chunk = sentence
        
        # Add the last chunk if it exists
        if current_chunk:
            chunks.append(current_chunk.strip())
        
        return chunks

    def text_to_speech(self, 
                      text_content: str, 
                      lesson_title: str = None,
                      voice_id: str = "21m00Tcm4TlvDq8ikWAM",  # Rachel voice (default)
                      model_id: str = "eleven_multilingual_v2") -> Dict:
        """Convert text to speech using ElevenLabs API"""
        try:
            # Create cache key
            cache_key = hashlib.md5(f"{text_content}{voice_id}{model_id}".encode()).hexdigest()
            
            if lesson_title:
                filename = f"{lesson_title.replace(' ', '_').replace('/', '_')}_{cache_key[:8]}.mp3"
            else:
                filename = f"elevenlabs_{cache_key[:8]}.mp3"
            
            cache_file = self.cache_dir / filename
            
            # Check if already cached
            if cache_file.exists():
                self.logger.info(f"Using cached audio: {cache_file}")
                file_size = cache_file.stat().st_size / 1024 / 1024  # MB
                return {
                    'audio_file': str(cache_file),
                    'cached': True,
                    'file_size_mb': round(file_size, 2),
                    'voice_id': voice_id,
                    'model_id': model_id
                }
            
            # Check character count and user limits
            char_count = len(text_content)
            user_info = self.get_user_info()
            
            if user_info and char_count > user_info.get('characters_remaining', 0):
                self.logger.warning(f"Text length ({char_count}) exceeds remaining characters ({user_info.get('characters_remaining', 0)})")
                # Generate only first chunk to stay within limits
                chunks = self.split_text_into_chunks(text_content)
                text_content = chunks[0] if chunks else text_content[:self.max_chunk_size]
                char_count = len(text_content)
                self.logger.info(f"Using first chunk only: {char_count} characters")
            
            # Generate speech
            self.logger.info(f"Generating speech with ElevenLabs ({char_count} characters)")
            
            # Prepare request payload
            payload = {
                "text": text_content,
                "model_id": model_id,
                "voice_settings": self.voice_settings
            }
            
            # Make API request
            response = requests.post(
                f"{self.base_url}/text-to-speech/{voice_id}",
                json=payload,
                headers=self.headers,
                timeout=300  # 5 minutes timeout for longer texts
            )
            
            response.raise_for_status()
            
            # Save audio to cache
            with open(cache_file, 'wb') as f:
                f.write(response.content)
            
            file_size = cache_file.stat().st_size / 1024 / 1024  # MB
            self.logger.info(f"Audio generated and saved: {cache_file} ({file_size:.2f} MB)")
            
            return {
                'audio_file': str(cache_file),
                'cached': False,
                'file_size_mb': round(file_size, 2),
                'character_count': char_count,
                'voice_id': voice_id,
                'model_id': model_id,
                'characters_used': char_count
            }
            
        except requests.exceptions.RequestException as e:
            if hasattr(e, 'response') and e.response is not None:
                error_detail = e.response.text
                self.logger.error(f"ElevenLabs API error: {e} - {error_detail}")
                raise Exception(f"ElevenLabs API error: {error_detail}")
            else:
                self.logger.error(f"Network error: {e}")
                raise Exception(f"Network error: {str(e)}")
        except Exception as e:
            self.logger.error(f"Error generating speech: {e}")
            raise Exception(f"Failed to generate speech: {str(e)}")

    def generate_chunked_lecture(self, 
                                text_content: str, 
                                lesson_title: str,
                                voice_id: str = "21m00Tcm4TlvDq8ikWAM",
                                max_chunks: int = 5) -> Dict:
        """Generate audio for long lectures by chunking"""
        try:
            chunks = self.split_text_into_chunks(text_content)
            total_char_count = len(text_content)
            
            # Limit chunks to avoid excessive API usage
            if len(chunks) > max_chunks:
                self.logger.warning(f"Text has {len(chunks)} chunks, limiting to {max_chunks} chunks")
                chunks = chunks[:max_chunks]
            
            self.logger.info(f"Generating chunked lecture: {len(chunks)} chunks, {total_char_count} total characters")
            
            audio_files = []
            total_size = 0
            total_chars_processed = 0
            
            for i, chunk in enumerate(chunks):
                chunk_filename = f"{lesson_title.replace(' ', '_').replace('/', '_')}_chunk_{i+1}.mp3"
                
                # Generate audio for this chunk
                try:
                    chunk_result = self.text_to_speech(
                        chunk, 
                        f"{lesson_title}_chunk_{i+1}",
                        voice_id=voice_id
                    )
                    
                    audio_files.append({
                        'file': chunk_result['audio_file'],
                        'chunk_number': i + 1,
                        'size_mb': chunk_result['file_size_mb'],
                        'character_count': len(chunk),
                        'cached': chunk_result['cached']
                    })
                    
                    total_size += chunk_result['file_size_mb']
                    total_chars_processed += len(chunk)
                    
                    self.logger.info(f"Generated chunk {i+1}/{len(chunks)}: {chunk_filename}")
                    
                    # Small delay to be respectful to API
                    if not chunk_result['cached']:
                        time.sleep(1)
                        
                except Exception as e:
                    self.logger.error(f"Failed to generate chunk {i+1}: {e}")
                    # Continue with other chunks
                    continue
            
            return {
                'audio_files': audio_files,
                'total_chunks': len(audio_files),
                'total_size_mb': round(total_size, 2),
                'total_characters_processed': total_chars_processed,
                'voice_id': voice_id,
                'success_rate': f"{len(audio_files)}/{len(chunks)}"
            }
            
        except Exception as e:
            self.logger.error(f"Error generating chunked lecture: {e}")
            return {'error': str(e)}

    def get_voice_preview(self, voice_id: str) -> Optional[str]:
        """Get preview URL for a voice"""
        voices = self.get_available_voices()
        for voice in voices:
            if voice['voice_id'] == voice_id:
                return voice.get('preview_url')
        return None

    def clone_voice_from_file(self, name: str, audio_file_path: str, description: str = "") -> Dict:
        """Clone a voice from an audio file (requires paid subscription)"""
        try:
            # This is for voice cloning - requires subscription
            files = {
                'files': (audio_file_path, open(audio_file_path, 'rb'), 'audio/mpeg')
            }
            
            data = {
                'name': name,
                'description': description
            }
            
            response = requests.post(
                f"{self.base_url}/voices/add",
                headers={"xi-api-key": self.api_key},
                data=data,
                files=files
            )
            
            response.raise_for_status()
            return response.json()
            
        except Exception as e:
            self.logger.error(f"Error cloning voice: {e}")
            return {'error': str(e)}

    def get_audio_history(self) -> List[Dict]:
        """Get history of generated audio"""
        try:
            response = requests.get(f"{self.base_url}/history", headers=self.headers)
            response.raise_for_status()
            
            history_data = response.json()
            return history_data.get('history', [])
            
        except Exception as e:
            self.logger.error(f"Error fetching history: {e}")
            return []