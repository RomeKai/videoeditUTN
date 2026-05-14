from moviepy import CompositeVideoClip, ColorClip, VideoClip
from .interface import BaseLayout
from ..face_tracker import FaceTracker
import logging

logger = logging.getLogger(__name__)

class VersusLayout(BaseLayout):
    """
    Layout 'Versus': Vertical split (Top/Bottom or Left/Right) for 2 people.
    Optimized for comparisons or dual guests.
    """
    def apply(self, clip):
        target_w, target_h = self.target_w, self.target_h
        
        # Split screen half height
        pane_h = target_h // 2
        
        # In a real VS, we usually have two sources or one source with 2 people.
        # If it's one source, we extract two different crops.
        
        # 1. Detect 2 main focus areas (Person A and Person B)
        # For simplicity, we'll take Left half and Right half of source
        # and fit them into Top and Bottom of target.
        
        # Person A (Top)
        p1 = clip.cropped(x1=0, y1=0, width=clip.w//2, height=clip.h)
        p1 = p1.resized(width=target_w, height=pane_h)
        p1 = p1.with_position(('center', 'top'))
        
        # Person B (Bottom)
        p2 = clip.cropped(x1=clip.w//2, y1=0, width=clip.w//2, height=clip.h)
        p2 = p2.resized(width=target_w, height=pane_h)
        p2 = p2.with_position(('center', 'bottom'))
        
        bg = ColorClip(size=(target_w, target_h), color=(0,0,0), duration=clip.duration)
        final = CompositeVideoClip([bg, p1, p2], size=(target_w, target_h))
        
        if clip.audio:
            final = final.with_audio(clip.audio)
            
        return final

class ActiveSpeakerLayout(BaseLayout):
    """
    Layout 'ActiveSpeaker': Automatically switches focus to whoever is talking.
    """
    def apply(self, clip):
        target_w, target_h = self.target_w, self.target_h
        
        # Default behavior: Just center fit if no tracking is provided
        # In a future update, this will use SpeakerTrackingEngine
        main = clip.resized(height=target_h)
        if main.w < target_w:
            main = clip.resized(width=target_w)
            
        main = main.cropped(x_center=main.w//2, y_center=main.h//2, width=target_w, height=target_h)
        
        if clip.audio:
            main = main.with_audio(clip.audio)
            
        return main
