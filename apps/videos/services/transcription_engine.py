import whisper
import os
import logging
from typing import List, Dict, Any

logger = logging.getLogger(__name__)

class TranscriptionEngine:
    def __init__(self, model_size: str = "tiny"):
        """
        model_size: 'tiny', 'base', 'small', 'medium', 'large'
        """
        self.model_size = model_size
        self._model = None

    @property
    def model(self):
        # Lazy Loading: El modelo pesa ( ~500MB a 2GB), solo lo cargamos si se usa.
        if self._model is None:
            logger.info(f"🧠 [TranscriptionEngine] Cargando modelo Whisper ({self.model_size})...")
            self._model = whisper.load_model(self.model_size)
        return self._model

    def transcribe(self, audio_path: str) -> List[Dict[str, Any]]:
        """
        Recibe ruta de un archivo de audio/video. 
        Retorna lista de segmentos limpios.
        """
        if not os.path.exists(audio_path):
            raise FileNotFoundError(f"Archivo de audio no encontrado: {audio_path}")

        logger.info(f"🎙️ [TranscriptionEngine] Iniciando transcripción...")
        
        # fp16=False es crucial para CPU. Si usas GPU, puedes quitarlo.
        try:
            result = self.model.transcribe(audio_path, fp16=False)
        except Exception as e:
            logger.error(f"Error crítico en Whisper: {e}")
            raise e
        
        # Limpiamos la salida para desacoplar Whisper de tu lógica
        segments = [
            {
                "start": seg["start"],
                "end": seg["end"],
                "text": seg["text"].strip()
            }
            for seg in result["segments"]
        ]
        
        logger.info(f"🎙️ [TranscriptionEngine] Transcripción completada: {len(segments)} segmentos.")
        return segments