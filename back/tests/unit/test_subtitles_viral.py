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

def test_viral_style_subtitles():
    print("🚀 Iniciando Test ESTILO VIRAL (Rápido + Emojis Solos)...")
    
    # Simulación de lo que Whisper entregaría
    raw_words = [
        {"start": 0.0, "end": 0.4, "text": "Este"},
        {"start": 0.4, "end": 0.8, "text": "video"},
        {"start": 0.8, "end": 1.2, "text": "es"},
        {"start": 1.2, "end": 1.6, "text": "fuego"},   # -> 🔥 Solo
        {"start": 1.6, "end": 2.0, "text": "tienes"},
        {"start": 2.0, "end": 2.4, "text": "que"},
        {"start": 2.4, "end": 2.8, "text": "ganar"},   # -> 🚀 Solo
        {"start": 2.8, "end": 3.2, "text": "mucho"},
        {"start": 3.2, "end": 3.6, "text": "dinero"},   # -> 💰 Solo
        {"start": 3.6, "end": 4.5, "text": "ahora mismo"},
    ]

    # Motor configurado con fuente GRANDE y color llamativo
    subtitler = SubtitleEngine(
        color="#FFFF00",
        with_emojis=True,
        size_type="large",
        position_type="center" # Al centro para que no haya duda de recortes
    )

    # Agrupar con lógica viral: máx 3 palabras + emoji_map
    segments = TranscriptionEngine.group_words(raw_words, max_words=3, emoji_map=subtitler.EMOJI_MAP)
    
    # Mostrar segmentación en consola
    for s in segments:
        label = "EMOJI" if s.get("is_emoji") else "TEXTO"
        print(f"  [{label}] {s['start']:.1f}s - {s['end']:.1f}s: {s['text']}")

    # Fondo 9:16 (Vertical)
    bg_clip = ColorClip(size=(720, 1280), color=(15, 15, 15), duration=5)
    
    # Renderizar
    final_clip = subtitler.add_subtitles(bg_clip, segments)
    output_path = "ESTILO_VIRAL_SUBTITULOS.mp4"
    final_clip.write_videofile(output_path, fps=24, logger=None)

    print(f"✅ Test VIRAL finalizado: {output_path}")

if __name__ == "__main__":
    test_viral_style_subtitles()
