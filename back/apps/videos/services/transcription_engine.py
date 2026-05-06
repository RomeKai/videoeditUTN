import whisper
import os
import logging
from typing import List, Dict, Any, Optional

logger = logging.getLogger(__name__)

class TranscriptionEngine:
    """
    Service for transcribing audio/video using OpenAI's Whisper.
    Supports word-level timestamps and segment grouping.
    """
    def __init__(self, model_size: str = "tiny"):
        """
        Args:
            model_size: 'tiny', 'base', 'small', 'medium', 'large'
        """
        self.model_size = model_size
        self._model = None

    @property
    def model(self):
        """
        Lazy Loading: The model size ranges from ~500MB to 2GB. 
        Only loads into memory when needed.
        """
        if self._model is None:
            logger.info(f"🧠 [TranscriptionEngine] Loading Whisper model ({self.model_size})...")
            self._model = whisper.load_model(self.model_size)
        return self._model

    def transcribe(self, audio_path: str, word_timestamps: bool = True) -> List[Dict[str, Any]]:
        """
        Transcribes an audio or video file.
        Returns a list of clean transcription segments.
        """
        if not os.path.exists(audio_path):
            raise FileNotFoundError(f"Audio file not found: {audio_path}")

        logger.info(f"🎙️ [TranscriptionEngine] Starting transcription (word_timestamps={word_timestamps})...")
        
        try:
            # fp16=False is crucial for CPU inference. 
            result = self.model.transcribe(audio_path, fp16=False, word_timestamps=word_timestamps)
        except Exception as e:
            logger.error(f"Critical error during Whisper transcription: {e}")
            raise e
        
        if word_timestamps:
            return self._process_word_level_segments(result)
        
        # Fallback to standard segments if word-level precision is not requested
        segments: List[Dict[str, Any]] = []
        for seg in result.get("segments", []):
            if isinstance(seg, dict):
                segments.append({
                    "start": seg.get("start"),
                    "end": seg.get("end"),
                    "text": str(seg.get("text", "")).strip()
                })
        
        logger.info(f"🎙️ [TranscriptionEngine] Transcription completed: {len(segments)} segments.")
        return segments

    def _process_word_level_segments(self, result: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        Extracts individual words with their respective timestamps from all segments.
        """
        all_words = []
        for segment in result.get("segments", []):
            if isinstance(segment, dict):
                for word in segment.get("words", []):
                    if isinstance(word, dict):
                        all_words.append({
                            "start": word.get("start"),
                            "end": word.get("end"),
                            "text": str(word.get("word", "")).strip()
                        })
        return all_words

    @staticmethod
    def group_words(words: List[Dict[str, Any]], max_words: int = 3, emoji_map: Optional[Dict[str, str]] = None) -> List[Dict[str, Any]]:
        """
        Groups words into manageable segments. 
        If a word matches the emoji_map, it creates a standalone emoji segment.
        """
        if not words:
            return []
            
        grouped = []
        buffer = []
        
        for w in words:
            # Normalize the word to look it up in the emoji map
            clean_word = "".join(e for e in w["text"].lower() if e.isalnum())
            
            if emoji_map and clean_word in emoji_map:
                # 1. Close and save existing buffer
                if buffer:
                    grouped.append({
                        "start": buffer[0]["start"],
                        "end": buffer[-1]["end"],
                        "text": " ".join([x["text"] for x in buffer]),
                        "is_emoji": False
                    })
                    buffer = []
                
                # 2. Create high-impact emoji segment
                grouped.append({
                    "start": w["start"],
                    "end": w["end"],
                    "text": emoji_map[clean_word],
                    "is_emoji": True
                })
            else:
                # Standard word, add to buffer
                buffer.append(w)
                # Close segment if word limit reached (e.g., 3 words)
                if len(buffer) >= max_words:
                    grouped.append({
                        "start": buffer[0]["start"],
                        "end": buffer[-1]["end"],
                        "text": " ".join([x["text"] for x in buffer]),
                        "is_emoji": False
                    })
                    buffer = []
        
        # Save remaining words in the buffer
        if buffer:
            grouped.append({
                "start": buffer[0]["start"],
                "end": buffer[-1]["end"],
                "text": " ".join([x["text"] for x in buffer]),
                "is_emoji": False
            })
            
        return grouped
