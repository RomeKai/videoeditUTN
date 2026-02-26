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

def test_stress_subtitles():
    print("🚀 Iniciando STRESS TEST de Subtítulos...")
    
    # Palabras difíciles y oraciones largas
    raw_words = [
        {"start": 0.0, "end": 1.5, "text": "GIGANTE"}, # Letras con trazos bajos
        {"start": 1.5, "end": 3.0, "text": "joya"},    # Letras con trazos bajos
        {"start": 3.0, "end": 4.5, "text": "fuego"},   # EMOJI 🔥
        {"start": 4.5, "end": 7.0, "text": "ESTE ES UN TEXTO EXTREMADAMENTE LARGO PARA PROBAR QUE EL SALTO DE LINEA FUNCIONA Y EL SI NO SE CORTA"},
        {"start": 7.0, "end": 8.5, "text": "dinero"},  # EMOJI 💰
        {"start": 8.5, "end": 10.0, "text": "EXITO"},  # EMOJI 🚀
    ]

    # Motor configurado en modo GRANDE y abajo (Bottom)
    subtitler = SubtitleEngine(
        color="#FFFF00",
        with_emojis=True,
        size_type="large",
        position_type="bottom"
    )

    # Agrupar (permitimos hasta 6 palabras para forzar el ancho)
    segments = TranscriptionEngine.group_words(raw_words, max_words=10, emoji_map=subtitler.EMOJI_MAP)
    
    # Fondo 9:16 (Vertical)
    bg_clip = ColorClip(size=(720, 1280), color=(20, 20, 20), duration=10)
    
    # Renderizar
    final_clip = subtitler.add_subtitles(bg_clip, segments)
    output_path = "STRESS_TEST_SUBTITULOS.mp4"
    final_clip.write_videofile(output_path, fps=24, logger=None)

    print(f"✅ Stress Test finalizado: {output_path}")

if __name__ == "__main__":
    test_stress_subtitles()
