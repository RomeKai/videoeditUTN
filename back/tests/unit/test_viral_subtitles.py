import os
import sys
import django
from moviepy import VideoFileClip

# Configurar Django para poder importar los servicios
sys.path.append(os.getcwd())
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'backend.settings.base')

# Mock de settings si es necesario o cargar django
try:
    django.setup()
except Exception as e:
    print(f"Error cargando Django: {e}")

from apps.videos.services.subtitle_engine import SubtitleEngine
from apps.videos.services.transcription_engine import TranscriptionEngine

def test_rendering():
    # 1. Datos de prueba
    # video_path = "media/videos/raw/2026/02/531f3c0b-7180-4cba-9a36-b34b45bbdb25.mp4"
    video_path = "media/videos/clips/temp/clip_380df32c-4cf7-4b3e-a780-a57cf987f458.mp4"
    if not os.path.exists(video_path):
        print(f"No se encontró el video en {video_path}")
        return

    print("🚀 Iniciando Test de Subtítulos Pro...")
    
    # 2. Simular segmentos de palabras (lo que vendría de Whisper)
    raw_words = [
        {"start": 0.5, "end": 1.0, "text": "Este"},
        {"start": 1.0, "end": 1.5, "text": "video"},
        {"start": 1.5, "end": 2.0, "text": "es"},
        {"start": 2.0, "end": 2.5, "text": "fuego"},
        {"start": 2.5, "end": 3.0, "text": "puro"},
        {"start": 3.0, "end": 3.5, "text": "y"},
        {"start": 3.5, "end": 4.0, "text": "ganar"},
        {"start": 4.0, "end": 4.5, "text": "mucho"},
        {"start": 4.5, "end": 5.0, "text": "dinero"},
    ]

    # 3. Agrupar palabras (Max 3 como pidió el usuario)
    grouped_segments = TranscriptionEngine.group_words(raw_words, max_words=3)
    print(f"📦 Segmentos agrupados: {len(grouped_segments)}")

    # 4. Configurar Motor de Subtítulos (Con Emojis y Color)
    subtitler = SubtitleEngine(
        color="#00FF00", # Verde neón para testear
        with_emojis=True,
        font_size=80
    )

    # 5. Procesar Video
    with VideoFileClip(video_path).subclipped(0, 6) as clip:
        # Aplicar subtítulos
        final_clip = subtitler.add_subtitles(clip, grouped_segments)
        
        output_path = "test_viral_subtitles.mp4"
        print(f"🎬 Renderizando clip de prueba en {output_path}...")
        
        final_clip.write_videofile(
            output_path,
            codec='libx264',
            audio_codec='aac',
            fps=24,
            logger=None
        )

    print("✅ Test finalizado con éxito.")

if __name__ == "__main__":
    test_rendering()
