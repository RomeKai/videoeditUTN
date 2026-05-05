import os
import sys
import django
import logging
from moviepy import ColorClip

# Configurar Logging
logging.basicConfig(level=logging.INFO, format='%(levelname)s:%(name)s:%(message)s')

sys.path.append(os.getcwd())
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'backend.settings.base')

try:
    django.setup()
except Exception as e:
    print(f"Error cargando Django: {e}")

from apps.videos.services.subtitle_engine import SubtitleEngine
from apps.videos.services.transcription_engine import TranscriptionEngine

def test_visual_verification():
    print("🚀 Iniciando Verificación Visual: Texto + Emojis...")
    
    # 1. Simular palabras individuales
    raw_words = [
        {"start": 0.0, "end": 1.0, "text": "Este"},
        {"start": 1.0, "end": 2.0, "text": "texto"},
        {"start": 2.0, "end": 3.0, "text": "es"},
        {"start": 3.0, "end": 4.0, "text": "fuego"}, # EMOJI 🔥
        {"start": 4.0, "end": 5.0, "text": "ganar"}, # EMOJI 🚀 (en el mapa es cohete/win)
        {"start": 5.0, "end": 6.0, "text": "dinero"}, # EMOJI 💰
        {"start": 6.0, "end": 8.0, "text": "MIRA ESTE TEXTO LARGO PARA VER SI SE CORTA"},
    ]

    # 2. Configurar motor
    subtitler = SubtitleEngine(
        color="#FFFF00",
        with_emojis=True,
        size_type="large",
        position_type="center" # Lo ponemos al centro para verlo bien
    )

    # 3. Agrupar
    segments = TranscriptionEngine.group_words(raw_words, max_words=2, emoji_map=subtitler.EMOJI_MAP)
    
    # 4. Crear clip de fondo (9:16 vertical como en los virales)
    bg_clip = ColorClip(size=(720, 1280), color=(30, 30, 30), duration=8)
    
    # 5. Renderizar
    final_clip = subtitler.add_subtitles(bg_clip, segments)
    output_path = "VERIFICACION_VISUAL_SUBTITULOS.mp4"
    final_clip.write_videofile(output_path, fps=24, logger=None)

    print(f"✅ Verificación finalizada: {output_path}")

if __name__ == "__main__":
    test_visual_verification()
