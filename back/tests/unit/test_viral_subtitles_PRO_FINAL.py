import os
import sys
import django
import logging
from moviepy import VideoFileClip

# Configurar Logging para ver qué pasa con los emojis
logging.basicConfig(level=logging.INFO, format='%(levelname)s:%(name)s:%(message)s')

sys.path.append(os.getcwd())
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'backend.settings.base')

try:
    django.setup()
except Exception as e:
    print(f"Error cargando Django: {e}")

from apps.videos.services.subtitle_engine import SubtitleEngine
from apps.videos.services.transcription_engine import TranscriptionEngine

def test_rendering_high_speed():
    video_path = r"media\videos\clips\temp\clip_380df32c-4cf7-4b3e-a780-a57cf987f458.mp4"
    if not os.path.exists(video_path):
        print(f"No se encontró el video")
        return

    print("🚀 Iniciando Test ALTA VELOCIDAD: Ruptura por Emoji Solo...")
    
    # 1. Simular palabras individuales (Lo que Whisper entrega)
    raw_words = [
        {"start": 0.5, "end": 1.0, "text": "Este"},
        {"start": 1.0, "end": 1.5, "text": "video"},
        {"start": 1.5, "end": 2.0, "text": "es"},
        {"start": 2.0, "end": 2.4, "text": "fuego"}, # Palabra clave
        {"start": 2.4, "end": 3.0, "text": "ganar"},
        {"start": 3.0, "end": 3.5, "text": "mucho"},
        {"start": 3.5, "end": 4.0, "text": "dinero"}, # Palabra clave
        {"start": 4.0, "end": 5.0, "text": "increible"},
    ]

    # 2. Configurar motor para obtener el mapa de emojis
    subtitler = SubtitleEngine(
        color="#FFFF00",
        with_emojis=True,
        size_type="large",
        position_type="bottom"
    )

    # 3. Agrupar con la nueva lógica de ruptura
    segments = TranscriptionEngine.group_words(raw_words, max_words=3, emoji_map=subtitler.EMOJI_MAP)
    
    print(f"📦 Segmentos generados: {len(segments)}")
    for s in segments:
        tipo = "EMOJI" if s.get("is_emoji") else "TEXTO"
        print(f"  [{tipo}] ({s['start']:.1f}s - {s['end']:.1f}s): {s['text']}")

    # 4. Renderizar
    with VideoFileClip(video_path).subclipped(0, 6) as clip:
        final_clip = subtitler.add_subtitles(clip, segments)
        output_path = "test_viral_subtitles_HIGH_SPEED.mp4"
        final_clip.write_videofile(output_path, codec='libx264', audio_codec='aac', fps=24, logger=None)

    print(f"✅ Test Finalizado: {output_path}")

if __name__ == "__main__":
    test_rendering_high_speed()
