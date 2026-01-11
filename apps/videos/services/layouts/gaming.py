from moviepy import CompositeVideoClip, ColorClip
from .interface import BaseLayout

class SplitLayout(BaseLayout):
    def apply(self, clip):
        # Definir alturas
        cam_h = int(self.target_h * 0.30)
        game_h = self.target_h - cam_h
        
        # A. Cámara (Top)
        cam = clip.cropped(x1=clip.w/3, y1=0, width=clip.w/3, height=clip.h/3)
        cam = cam.resized(height=cam_h)
        cam = cam.with_position(('center', 'top'))

        # B. Gameplay (Bottom)
        game = clip.cropped(x_center=clip.w/2, y_center=clip.h/2, width=self.target_w, height=game_h)
        game = game.resized(height=game_h)
        game = game.with_position(('center', 'bottom'))

        # Fondo negro base
        bg = ColorClip(
            size=(self.target_w, self.target_h), 
            color=(0,0,0), 
            duration=clip.duration
        )
        
        return CompositeVideoClip(
            [bg, game, cam], 
            size=(self.target_w, self.target_h)
        )