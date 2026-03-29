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

    def transcribe(self, audio_path: str, word_timestamps: bool = True) -> List[Dict[str, Any]]:
        """
        Recibe ruta de un archivo de audio/video. 
        Retorna lista de segmentos limpios.
        """
        if not os.path.exists(audio_path):
            raise FileNotFoundError(f"Archivo de audio no encontrado: {audio_path}")

        logger.info(f"🎙️ [TranscriptionEngine] Iniciando transcripción (word_timestamps={word_timestamps})...")
        
        try:
            # fp16=False es crucial para CPU. Si usas GPU, puedes quitarlo.
            result = self.model.transcribe(audio_path, fp16=False, word_timestamps=word_timestamps)
        except Exception as e:
            logger.error(f"Error crítico en Whisper: {e}")
            raise e
        
        if word_timestamps:
            return self._process_word_level_segments(result)
        
        # Fallback a segmentos normales si no se pidieron palabras
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

    def _process_word_level_segments(self, result: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        Extrae todas las palabras individuales con sus tiempos de todos los segmentos.
        """
        all_words = []
        for segment in result.get("segments", []):
            for word in segment.get("words", []):
                all_words.append({
                    "start": word["start"],
                    "end": word["end"],
                    "text": word["word"].strip()
                })
        return all_words

    @staticmethod
    def group_words(words: List[Dict[str, Any]], max_words: int = 3, emoji_map: Dict[str, str] = None) -> List[Dict[str, Any]]:
        """
        Agrupa palabras. Si encuentra una palabra que está en emoji_map, 
        crea un segmento solo para ese emoji y reinicia el grupo.
        """
        if not words:
            return []
            
        grouped = []
        buffer = []
        
        for w in words:
            # Limpiamos la palabra para buscarla en el mapa
            clean_word = "".join(e for e in w["text"].lower() if e.isalnum())
            
            if emoji_map and clean_word in emoji_map:
                # 1. Si había texto acumulado antes, lo cerramos y guardamos
                if buffer:
                    grouped.append({
                        "start": buffer[0]["start"],
                        "end": buffer[-1]["end"],
                        "text": " ".join([x["text"] for x in buffer]),
                        "is_emoji": False
                    })
                    buffer = []
                
                # 2. Creamos el segmento flash del emoji (más rápido)
                grouped.append({
                    "start": w["start"],
                    "end": w["end"],
                    "text": emoji_map[clean_word],
                    "is_emoji": True
                })
            else:
                # Es palabra normal, la metemos al buffer
                buffer.append(w)
                # Si llegamos al límite de palabras (ej: 3), cerramos segmento
                if len(buffer) >= max_words:
                    grouped.append({
                        "start": buffer[0]["start"],
                        "end": buffer[-1]["end"],
                        "text": " ".join([x["text"] for x in buffer]),
                        "is_emoji": False
                    })
                    buffer = []
        
        # Guardamos lo que quede en el buffer
        if buffer:
            grouped.append({
                "start": buffer[0]["start"],
                "end": buffer[-1]["end"],
                "text": " ".join([x["text"] for x in buffer]),
                "is_emoji": False
            })
            
        return grouped