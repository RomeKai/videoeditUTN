import os
from django.conf import settings

# --- IMPORTACIONES LAZY (Blindaje) ---
# No importamos whisper/ffmpeg aquí arriba para no romper el contenedor WEB.
# Se importarán dentro de las funciones.

# Variable global para el modelo (solo existirá en el Worker)
model = None

class AIEngine:
    
    @staticmethod
    def extract_audio(video_path):
        """
        Extrae el audio del video mp4 y lo guarda como mp3 ligero.
        """
        # Importamos AQUÍ dentro, solo cuando se ejecuta la función
        import ffmpeg 
        
        base_name = os.path.splitext(video_path)[0]
        audio_path = f"{base_name}.mp3"
        
        print(f"🔊 Extrayendo audio de: {os.path.basename(video_path)}")
        
        try:
            (
                ffmpeg
                .input(video_path)
                .output(audio_path, format='mp3', acodec='libmp3lame', ab='128k')
                .overwrite_output()
                .run(quiet=True, capture_stdout=True, capture_stderr=True)
            )
            return audio_path
        except ffmpeg.Error as e:
            error_log = e.stderr.decode() if e.stderr else str(e)
            print(f"❌ Error ffmpeg: {error_log}")
            raise Exception(f"Fallo extracción audio: {error_log}")

    @staticmethod
    def transcribe_audio(audio_path):
        """
        Usa Whisper para obtener texto + timestamps.
        """
        # Importamos Whisper AQUÍ dentro
        import whisper
        global model

        # Cargar modelo (Singleton Pattern)
        if model is None:
            print("🧠 [AI ENGINE] Cargando modelo Whisper (base) por primera vez...")
            try:
                # fp16=False obligatorio para CPU
                model = whisper.load_model("base")
            except Exception as e:
                raise Exception(f"Error cargando Whisper: {e}")
            
        print(f"📝 Transcribiendo audio... (Paciencia, procesando con CPU)")
        
        result = model.transcribe(audio_path, fp16=False)
        
        print(f"✅ Transcripción lista: {len(result['segments'])} segmentos.")
        
        return {
            "full_text": result["text"].strip(),
            "language": result["language"],
            "segments": result["segments"]
        }