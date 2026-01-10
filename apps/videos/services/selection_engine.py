import json
import logging
from abc import ABC, abstractmethod
from django.conf import settings

# BORRAMOS: from openai import OpenAI (Esto rompía la Web)

logger = logging.getLogger(__name__)

# --- 1. LA INTERFAZ (STRATEGY INTERFACE) ---
class ClipSelectionStrategy(ABC):
    """
    Interfaz abstracta que deben cumplir todos los modelos de IA.
    """
    def __init__(self, api_key):
        # IMPORTACIÓN LAZY: Solo ocurre cuando se instancia la clase (en el Worker)
        from openai import OpenAI 
        self.client = OpenAI(api_key=api_key)

    @abstractmethod
    def get_model_name(self):
        pass

    @abstractmethod
    def get_max_tokens_context(self):
        pass

    def select_clips(self, prompt):
        """
        Método plantilla que ejecuta la llamada.
        """
        model = self.get_model_name()
        logger.info(f"🧠 Consultando al modelo: {model}")
        
        response = self.client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": "Eres una API JSON. Solo respondes JSON válido."},
                {"role": "user", "content": prompt}
            ],
            response_format={"type": "json_object"},
            temperature=0.7 
        )
        return response.choices[0].message.content

# --- 2. LAS ESTRATEGIAS CONCRETAS ---

class FastStrategy(ClipSelectionStrategy):
    """Estrategia Económica y Rápida (GPT-4o-mini)"""
    def get_model_name(self):
        return "gpt-4o-mini"
    
    def get_max_tokens_context(self):
        return 30000

class HighIQStrategy(ClipSelectionStrategy):
    """Estrategia Premium (GPT-4o)."""
    def get_model_name(self):
        return "gpt-4o"
    
    def get_max_tokens_context(self):
        return 50000

# --- 3. EL CONTEXTO (SELECTION ENGINE) ---

class SelectionEngine:
    
    @staticmethod
    def _get_strategy(level) -> ClipSelectionStrategy:
        """Factory Method simple para elegir la estrategia"""
        api_key = settings.OPENAI_API_KEY
        if not api_key:
            raise Exception("OPENAI_API_KEY faltante.")

        if level == 'smart':
            return HighIQStrategy(api_key)
        else:
            return FastStrategy(api_key)

    @staticmethod
    def _calculate_clip_count(duration_seconds):
        if not duration_seconds: return 3
        minutes = duration_seconds / 60
        if minutes < 2: return 1
        elif minutes < 5: return 2
        elif minutes < 15: return 4
        elif minutes < 30: return 8
        else: return 12

    @staticmethod
    def select_viral_clips(transcription_data, project_title, editing_style, duration, intelligence_level='fast'):
        """
        Cliente del patrón Strategy.
        """
        # 1. Obtenemos la estrategia (Esto activará el import lazy de OpenAI)
        strategy = SelectionEngine._get_strategy(intelligence_level)
        
        # 2. Preparamos datos
        target_clips = SelectionEngine._calculate_clip_count(duration)
        
        char_limit = strategy.get_max_tokens_context() * 3 
        full_text = transcription_data.get('full_text', '')[:char_limit]

        # 3. Prompt
        prompt = f"""
        Actúa como un editor experto.
        Video: "{project_title}". Estilo: {editing_style}.
        
        Objetivo: Encontrar los {target_clips} mejores momentos virales.
        
        Reglas:
        - Output JSON estricto.
        - Duración clips: 15-60s.
        - Score viralidad: 0-100.
        
        Transcripción:
        {full_text}
        
        Schema JSON esperado:
        {{ "clips": [ {{ "start": 0.0, "end": 10.0, "title": "...", "virality_score": 80, "reasoning": "..." }} ] }}
        """

        try:
            content = strategy.select_clips(prompt)
            data = json.loads(content)
            clips = data.get('clips', data) if isinstance(data, dict) else data
            return clips

        except Exception as e:
            logger.error(f"❌ Error en SelectionEngine: {e}")
            raise e