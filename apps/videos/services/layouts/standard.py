from moviepy import ColorClip, CompositeVideoClip
from .interface import BaseLayout

class FillLayout(BaseLayout):
    def apply(self, clip):
        # En v2 usamos 'cropped' en lugar de 'crop'
        return clip.cropped(
            x_center=clip.w/2, 
            y_center=clip.h/2, 
            width=self.target_w, 
            height=self.target_h
        )

class FitLayout(BaseLayout):
    def apply(self, clip):
        # 1. Redimensionar (resized)
        main = clip.resized(width=self.target_w)
        
        if main.h > self.target_h:
            main = clip.resized(height=self.target_h)
            
        # 2. Centrar (with_position)
        main = main.with_position("center")
        
        # 3. Fondo Negro
        bg = ColorClip(
            size=(self.target_w, self.target_h), 
            color=(0,0,0), 
            duration=clip.duration
        )
        
        return CompositeVideoClip([bg, main], size=(self.target_w, self.target_h))