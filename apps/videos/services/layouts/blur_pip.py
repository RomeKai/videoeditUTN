from moviepy import CompositeVideoClip
# En v2, importamos 'vfx' como el módulo que contiene todos los efectos
import moviepy.video.fx as vfx 
from .interface import BaseLayout

class BlurredLayout(BaseLayout):
    """
    Layout para MoviePy v2.0+
    - Capa principal nítida en el centro.
    - Fondo borroso y oscurecido llenando la pantalla 9:16.
    """
    def apply(self, clip):
        # 1. CAPA PRINCIPAL (Foreground)
        # Usamos .resized() y .with_position() (Sintaxis v2)
        main = clip.resized(width=self.target_w)
        
        # Si al ajustar al ancho se pasa de alto, recortamos un poco arriba/abajo
        if main.h > self.target_h:
            main = main.cropped(x_center=main.w/2, y_center=main.h/2, height=self.target_h)
            
        main = main.with_position("center")

        # 2. CAPA DE FONDO (Background)
        # Queremos que llene toda la altura (1920px usualmente)
        background = clip.resized(height=self.target_h)
        
        # Recortamos los lados sobrantes para que tenga el ancho exacto (1080px)
        background = background.cropped(x_center=background.w/2, width=self.target_w)
        
        # 3. APLICAR EFECTOS (Blur + Oscuridad)
        # En v2 usamos .with_effects([]) pasando una lista de efectos instanciados
        background = background.with_effects([
            vfx.GaussianBlur(sigma=25),    # Desenfoque fuerte
            vfx.MultiplyColor(factor=0.6)  # Oscurecer al 60%
        ])
        
        # 4. COMPOSICIÓN
        return CompositeVideoClip(
            [background, main], 
            size=(self.target_w, self.target_h)
        )