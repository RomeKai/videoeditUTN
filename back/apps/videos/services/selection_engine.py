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
    @abstractmethod
    def get_model_name(self) -> str:
        """Returns the specific model string."""
        pass

    @abstractmethod
    def get_max_tokens_context(self) -> int:
        """Returns the token limit for the context window."""
        pass

    @abstractmethod
    def select_clips(self, prompt: str) -> str:
        """
        Executes the AI request.
        """
        pass

# --- 2. CONCRETE STRATEGIES ---

class OpenAIStrategy(ClipSelectionStrategy):
    """Base for OpenAI models."""
    def __init__(self, api_key: str):
        from openai import OpenAI
        self.client = OpenAI(api_key=api_key)

    def select_clips(self, prompt: str) -> str:
        model = self.get_model_name()
        logger.info(f"🧠 Querying OpenAI model: {model}")
        
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

class FastStrategy(OpenAIStrategy):
    """Economic and Fast Strategy (GPT-4o-mini)."""
    def get_model_name(self) -> str:
        return "gpt-4o-mini"
    
    def get_max_tokens_context(self) -> int:
        return 30000

class HighIQStrategy(OpenAIStrategy):
    """Premium Intelligence Strategy (GPT-4o)."""
    def get_model_name(self) -> str:
        return "gpt-4o"
    
    def get_max_tokens_context(self) -> int:
        return 50000

class GeminiStrategy(ClipSelectionStrategy):
    """High-Context Strategy (Gemini 1.5 Flash). Optimized for massive transcripts."""
    def __init__(self, api_key: str):
        self.api_key = api_key

    def get_model_name(self) -> str:
        return "gemini-1.5-flash-latest"
    
    def get_max_tokens_context(self) -> int:
        return 1000000

    def select_clips(self, prompt: str) -> str:
        import requests
        logger.info(f"🧠 Querying Gemini model: {self.get_model_name()}")
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.get_model_name()}:generateContent?key={self.api_key}"
        
        payload = {
            "contents": [{
                "parts": [{"text": prompt}]
            }],
            "generationConfig": {
                "response_mime_type": "application/json"
            }
        }
        
        response = requests.post(url, json=payload)
        if response.status_code != 200:
            logger.error(f"Gemini API Error: {response.text}")
            response.raise_for_status()
        
        data = response.json()
        try:
            return data['candidates'][0]['content']['parts'][0]['text']
        except (KeyError, IndexError):
            raise ValueError(f"Unexpected response format from Gemini: {data}")

# --- 3. CONTEXT (SELECTION ENGINE) ---

class SelectionEngine:
    """
    Engine responsible for orchestrating viral moment detection using AI.
    """
    
    @staticmethod
    def _get_strategy(level: str) -> ClipSelectionStrategy:
        """Factory Method to select the appropriate AI strategy."""
        if level == 'pro' or level == 'gemini':
            api_key = getattr(settings, 'GEMINI_API_KEY', None)
            if not api_key:
                logger.warning("GEMINI_API_KEY missing, falling back to HighIQ OpenAI.")
                return SelectionEngine._get_strategy('smart')
            return GeminiStrategy(api_key)
            
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
        editing_style: str = 'dynamic', 
        duration: Optional[float] = None, 
        intelligence_level: str = 'fast'
    ) -> List[Dict[str, Any]]:
        """
        Main client method for viral clip selection.
        """
        # 1. Initialize strategy
        strategy = SelectionEngine._get_strategy(intelligence_level)
        
        # 2. Data Preparation
        target_clips = SelectionEngine._calculate_clip_count(duration)
        
        # Approximate character limit based on token context (avg 4 chars per token for safety)
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
            # Cleanup possible markdown code blocks from AI response
            if content.startswith("```json"):
                content = content.replace("```json", "").replace("```", "").strip()
            
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
