import streamlit as st
import pandas as pd
import os
import sys
from pathlib import Path
import logging
from datetime import datetime

# Add the current directory to Python path for imports
current_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, current_dir)

# Import our lecture agent modules
try:
    from src.core.content_processor import ContentProcessor
    from src.core.speech_generator import SpeechGenerator
    from src.integrations.supabase_client import SupabaseClient
except ImportError as e:
    st.error(f"Import error: {e}")
    st.error("Make sure you're running from the correct directory with all modules available.")
    st.stop()

# Configure logging to suppress verbose outputs
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("openai").setLevel(logging.WARNING)

# Streamlit page config
st.set_page_config(
    page_title="Teacher PDF Lecture Agent",
    page_icon="📄",
    layout="wide"
)

# Custom CSS for better styling
st.markdown("""
<style>
    .main-header {
        font-size: 2.5rem;
        font-weight: bold;
        text-align: center;
        margin-bottom: 2rem;
        color: #1e3a8a;
    }
    .teacher-info {
        background-color: #f0f9ff;
        border: 1px solid #0ea5e9;
        border-radius: 8px;
        padding: 1rem;
        margin-bottom: 2rem;
    }
    .lesson-card {
        border: 1px solid #e2e8f0;
        border-radius: 8px;
        padding: 1rem;
        margin: 0.5rem 0;
        background-color: #ffffff;
    }
    .pdf-badge {
        background-color: #dc2626;
        color: white;
        padding: 4px 8px;
        border-radius: 4px;
        font-size: 0.8rem;
        margin-left: 10px;
    }
    .course-badge {
        background-color: #059669;
        color: white;
        padding: 4px 8px;
        border-radius: 4px;
        font-size: 0.8rem;
        margin-left: 10px;
    }
    .stButton > button {
        width: 100%;
        margin: 5px 0;
    }
</style>
""", unsafe_allow_html=True)

# Initialize session state
if 'generated_scripts' not in st.session_state:
    st.session_state.generated_scripts = {}

if 'generated_audio' not in st.session_state:
    st.session_state.generated_audio = {}

@st.cache_resource
def init_agents():
    """Initialize the agents (cached to avoid recreating)"""
    try:
        db_client = SupabaseClient()
        content_processor = ContentProcessor()
        speech_generator = SpeechGenerator()
        return db_client, content_processor, speech_generator
    except Exception as e:
        st.error(f"Failed to initialize agents: {e}")
        return None, None, None

@st.cache_data(ttl=60)
def load_teacher_data():
    """Load teacher information and course data"""
    db_client, _, _ = init_agents()
    if db_client is None:
        return None, None
    
    try:
        teacher_info = db_client.get_teacher_info()
        teacher_courses_data = db_client.get_all_teacher_lessons_with_courses()
        return teacher_info, teacher_courses_data
    except Exception as e:
        st.error(f"Error loading teacher data: {e}")
        return None, None

def generate_lecture_content(lesson_id, lesson_title, pdf_url):
    """Generate lecture content from PDF URL"""
    _, content_processor, _ = init_agents()
    
    if content_processor is None:
        st.error("Content processor not available")
        return None
    
    try:
        with st.spinner(f"Processing PDF and generating lecture content for '{lesson_title}'..."):
            result = content_processor.create_lecture_script(pdf_url, lesson_title)
            st.session_state.generated_scripts[lesson_id] = result
            st.success("Lecture content generated successfully!")
            return result
    except Exception as e:
        st.error(f"Error generating lecture: {str(e)}")
        return None

def generate_and_play_audio(lesson_id, lesson_title):
    """Generate audio from lecture content and play it"""
    _, _, speech_generator = init_agents()
    
    if speech_generator is None:
        st.error("Speech generator not available")
        return None
    
    if lesson_id not in st.session_state.generated_scripts:
        st.warning("Please generate lecture content first!")
        return None
    
    script_content = st.session_state.generated_scripts[lesson_id]['script']
    
    try:
        with st.spinner(f"Generating audio for '{lesson_title}'..."):
            audio_result = speech_generator.text_to_speech(script_content, lesson_title)
            st.session_state.generated_audio[lesson_id] = audio_result
            return audio_result
    except Exception as e:
        st.error(f"Error generating audio: {str(e)}")
        return None

def display_lesson_content(lesson, course_title):
    """Display lesson content with generation options"""
    lesson_id = lesson['id']
    lesson_title = lesson['title']
    pdf_url = lesson['resources']
    
    with st.container():
        # Lesson header
        col1, col2 = st.columns([4, 1])
        with col1:
            st.markdown(f"#### {lesson_title}")
        with col2:
            st.markdown('<span class="pdf-badge">PDF</span>', unsafe_allow_html=True)
        
        # Lesson details
        col1, col2 = st.columns([3, 1])
        
        with col1:
            st.write(f"**Lesson ID:** {lesson_id}")
            if lesson.get('description'):
                desc = lesson.get('description', '')
                st.write(f"**Description:** {desc[:150]}{'...' if len(desc) > 150 else ''}")
            st.write(f"**PDF URL:** {pdf_url[:80]}{'...' if len(pdf_url) > 80 else ''}")
            st.write(f"**Order:** {lesson.get('order_index', 'N/A')}")
        
        with col2:
            if st.button("View PDF", key=f"pdf_{lesson_id}"):
                st.code(pdf_url)
        
        # Action buttons
        col1, col2 = st.columns(2)
        
        with col1:
            if st.button(
                "Generate Lecture Content", 
                key=f"generate_{lesson_id}", 
                type="primary"
            ):
                generate_lecture_content(lesson_id, lesson_title, pdf_url)
        
        with col2:
            if st.button(
                "Generate & Play Audio", 
                key=f"audio_{lesson_id}", 
                type="secondary"
            ):
                generate_and_play_audio(lesson_id, lesson_title)
        
        # Display generated content
        if lesson_id in st.session_state.generated_scripts:
            script_data = st.session_state.generated_scripts[lesson_id]
            
            st.markdown("##### Generated Lecture Content:")
            
            with st.expander("View Full Lecture Script"):
                st.text_area(
                    "Lecture Script:", 
                    script_data['script'], 
                    height=300,
                    disabled=True,
                    key=f"script_display_{lesson_id}"
                )
            
            st.download_button(
                "Download Script",
                script_data['script'],
                file_name=f"{lesson_title.replace(' ', '_')}_script.txt",
                mime="text/plain",
                key=f"download_script_{lesson_id}"
            )
        
        # Display audio player
        if lesson_id in st.session_state.generated_audio:
            audio_data = st.session_state.generated_audio[lesson_id]
            
            st.markdown("##### Generated Audio:")
            
            if os.path.exists(audio_data['audio_file']):
                st.audio(audio_data['audio_file'], format='audio/mp3')
                
                col1, col2, col3 = st.columns(3)
                with col1:
                    st.metric("File Size", f"{audio_data['file_size_mb']} MB")
                with col2:
                    duration = audio_data['file_size_mb'] * 1.2
                    st.metric("Est. Duration", f"{duration:.1f} min")
                with col3:
                    cost = audio_data.get('estimated_cost', 0)
                    st.metric("Cost", f"${cost:.4f}")
                
                with open(audio_data['audio_file'], 'rb') as f:
                    audio_bytes = f.read()
                st.download_button(
                    "Download Audio",
                    audio_bytes,
                    file_name=f"{lesson_title.replace(' ', '_')}_lecture.mp3",
                    mime="audio/mpeg",
                    key=f"download_audio_{lesson_id}"
                )
            else:
                st.error("Audio file not found")

def main():
    # Header
    st.markdown('<h1 class="main-header">Teacher PDF Lecture Agent</h1>', unsafe_allow_html=True)
    
    # Initialize agents
    db_client, content_processor, speech_generator = init_agents()
    
    if db_client is None:
        st.error("Failed to initialize. Check your .env file and database connection.")
        return
    
    # Load teacher data
    with st.spinner("Loading teacher information and courses..."):
        teacher_info, teacher_courses_data = load_teacher_data()
    
    if not teacher_info or 'error' in teacher_info:
        st.error("Could not load teacher information. Please check if the teacher ID exists in the database.")
        return
    
    if not teacher_courses_data or 'error' in teacher_courses_data:
        st.error(f"Could not load course data: {teacher_courses_data.get('error', 'Unknown error')}")
        return
    
    # Display teacher information
    st.markdown('<div class="teacher-info">', unsafe_allow_html=True)
    st.markdown(f"**Teacher:** {teacher_info.get('name', 'N/A')} ({teacher_info.get('email', 'N/A')})")
    st.markdown(f"**Teacher ID:** {db_client.teacher_id}")
    st.markdown(f"**School ID:** {teacher_courses_data['school_id']}")
    st.markdown(f"**Total Courses:** {teacher_courses_data['total_courses']}")
    st.markdown(f"**Total PDF Lessons:** {teacher_courses_data['total_pdf_lessons']}")
    st.markdown('</div>', unsafe_allow_html=True)
    
    if teacher_courses_data['total_pdf_lessons'] == 0:
        st.warning("No PDF lessons found for this teacher's courses.")
        return
    
    # Sidebar with course summary
    with st.sidebar:
        st.header("Course Summary")
        for course in teacher_courses_data['courses']:
            if course['lesson_count'] > 0:
                st.markdown(f"**{course['course_title']}**")
                st.write(f"Lessons: {course['lesson_count']}")
                st.write("---")
    
    st.markdown("---")
    
    # Display courses and lessons
    for course in teacher_courses_data['courses']:
        if course['lesson_count'] == 0:
            continue
            
        # Course header
        st.markdown(f"## {course['course_title']}")
        if course['course_description']:
            st.markdown(f"**Description:** {course['course_description']}")
        st.markdown(f"**Course ID:** {course['course_id']}")
        st.markdown(f"**PDF Lessons:** {course['lesson_count']}")
        st.markdown("---")
        
        # Display lessons for this course
        for lesson in course['lessons']:
            display_lesson_content(lesson, course['course_title'])
        
        st.markdown("---")
    
    # Footer
    st.markdown("""
    ### Instructions:
    1. **Generate Lecture Content**: Convert PDF into a structured lecture script
    2. **Generate & Play Audio**: Convert the lecture script into speech (requires lecture content first)
    3. Lessons are organized by course
    4. Only PDF resources are shown
    5. Download options available for scripts and audio
    """)

if __name__ == "__main__":
    main()