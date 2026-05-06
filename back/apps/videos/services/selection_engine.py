import json
import logging
from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional
from django.conf import settings

logger = logging.getLogger(__name__)

# --- 1. STRATEGY INTERFACE ---
class ClipSelectionStrategy(ABC):
    """
    Abstract interface for AI selection models following the Strategy Pattern.
    """
    def __init__(self, api_key: str):
        # LAZY IMPORT: Only happens when the class is instantiated (typically in the Celery Worker)
        from openai import OpenAI 
        self.client = OpenAI(api_key=api_key)

    @abstractmethod
    def get_model_name(self) -> str:
        """Returns the specific model string (e.g., 'gpt-4o')."""
        pass

    @abstractmethod
    def get_max_tokens_context(self) -> int:
        """Returns the token limit for the context window."""
        pass

    def select_clips(self, prompt: str) -> str:
        """
        Template method to execute the AI request.
        """
        model = self.get_model_name()
        logger.info(f"🧠 Querying AI model: {model}")
        
        response = self.client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": "You are a JSON API. You only respond with valid JSON objects."},
                {"role": "user", "content": prompt}
            ],
            response_format={"type": "json_object"},
            temperature=0.7 
        )
        
        content = response.choices[0].message.content
        if content is None:
            raise ValueError(f"AI Model {model} returned an empty response.")
            
        return content

# --- 2. CONCRETE STRATEGIES ---

class FastStrategy(ClipSelectionStrategy):
    """Economic and Fast Strategy (GPT-4o-mini)."""
    def get_model_name(self) -> str:
        return "gpt-4o-mini"
    
    def get_max_tokens_context(self) -> int:
        return 30000

class HighIQStrategy(ClipSelectionStrategy):
    """Premium Intelligence Strategy (GPT-4o)."""
    def get_model_name(self) -> str:
        return "gpt-4o"
    
    def get_max_tokens_context(self) -> int:
        return 50000

# --- 3. CONTEXT (SELECTION ENGINE) ---

class SelectionEngine:
    """
    Engine responsible for orchestrating viral moment detection using AI.
    """
    
    @staticmethod
    def _get_strategy(level: str) -> ClipSelectionStrategy:
        """Factory Method to select the appropriate AI strategy."""
        api_key = getattr(settings, 'OPENAI_API_KEY', None)
        if not api_key:
            raise ValueError("Configuration Error: OPENAI_API_KEY is missing in settings.")

        if level == 'smart':
            return HighIQStrategy(api_key)
        else:
            return FastStrategy(api_key)

    @staticmethod
    def _calculate_clip_count(duration_seconds: Optional[float]) -> int:
        """Estimates ideal number of clips based on source duration."""
        if not duration_seconds: 
            return 3
            
        minutes = duration_seconds / 60
        if minutes < 2: return 1
        elif minutes < 5: return 2
        elif minutes < 15: return 4
        elif minutes < 30: return 8
        else: return 12

    @staticmethod
    def select_viral_clips(
        transcription_data: Dict[str, Any], 
        project_title: str, 
        editing_style: str, 
        duration: Optional[float], 
        intelligence_level: str = 'fast'
    ) -> List[Dict[str, Any]]:
        """
        Main client method for viral clip selection.
        """
        # 1. Initialize strategy (triggers lazy OpenAI import)
        strategy = SelectionEngine._get_strategy(intelligence_level)
        
        # 2. Data Preparation
        target_clips = SelectionEngine._calculate_clip_count(duration)
        
        # Approximate character limit based on token context (avg 3 chars per token)
        char_limit = strategy.get_max_tokens_context() * 3 
        full_text = str(transcription_data.get('full_text', ''))[:char_limit]

        # 3. AI Prompt Construction
        prompt = f"""
        Act as an expert video editor.
        Project: "{project_title}". Editing Style: {editing_style}.
        
        Goal: Identify the top {target_clips} most viral moments from the transcript.
        
        Rules:
        - Strict JSON output.
        - Clip duration: 15-60 seconds.
        - Virality score: 0-100.
        
        Transcript:
        {full_text}
        
        Expected JSON Schema:
        {{ "clips": [ {{ "start": 0.0, "end": 10.0, "title": "...", "virality_score": 80, "reasoning": "..." }} ] }}
        """

        try:
            content = strategy.select_clips(prompt)
            data = json.loads(content)
            
            # Extract list from wrapper object if present
            clips = data.get('clips', data) if isinstance(data, dict) else data
            
            if not isinstance(clips, list):
                logger.warning("AI returned a non-list format for clips. Wrapping in list.")
                return [clips] if clips else []
                
            return clips

        except Exception as e:
            logger.error(f"❌ SelectionEngine Error: {e}")
            raise e
