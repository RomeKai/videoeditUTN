import os
import sys
import logging
from moviepy import ColorClip, TextClip

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def run_diagnostics():
    print("\n" + "="*50)
    print("🔍 INICIANDO DIAGNÓSTICO DEL ENTORNO MOVIEPY")
    print("="*50)

    # 1. Prueba de Motor de Texto y Resolución
    print("\n[PRUEBA 1] Generando TextClip de tamaño 40...")
    try:
        # Intentamos generar un texto como lo haría el backend
        txt = TextClip(
            text="Prueba de Resolución y Texto",
            font="Arial", # Usamos genérica para no depender de rutas locales
            font_size=40,
            color="white"
        )
        print(f"✅ TextClip generado con éxito.")
        print(f"📏 Tamaño real generado por el motor: Ancho={txt.w}px, Alto={txt.h}px")
        
        # Análisis: Si font_size es 40, el alto debería rondar los 40-50px. 
        # Si el alto es algo absurdo como 300px, descubrimos por qué tus subtítulos son gigantes.
        if txt.h > 100:
            print("❌ ALERTA ROJA: El motor gráfico está escalando la fuente masivamente.")
        else:
            print("✅ El motor de texto está escalando correctamente.")
            
    except Exception as e:
        print(f"❌ FALLO CRÍTICO EN TEXTCLIP: {e}")

    # 2. Prueba de Canvas
    print("\n[PRUEBA 2] Simulando Canvas 9:16 (1080x1920)...")
    try:
        bg = ColorClip(size=(1080, 1920), color=(0,0,0), duration=1)
        print(f"✅ Canvas generado. Resolucion: {bg.w}x{bg.h}")
    except Exception as e:
        print(f"❌ FALLO EN CANVAS: {e}")

    print("\n" + "="*50)
    print("🏁 DIAGNÓSTICO FINALIZADO")
    print("="*50 + "\n")

if __name__ == "__main__":
    run_diagnostics()
    