from abc import ABC, abstractmethod

class BaseLayout(ABC):
    """
    Interfaz abstracta para estrategias de layout.
    """
    
    def __init__(self, target_w, target_h, use_facetracking=False, gameplay_pos='center', camera_selection_pos='center', manual_camera_coords=None):
        self.target_w = int(target_w)
        self.target_h = int(target_h)
        self.use_facetracking = use_facetracking
        self.gameplay_pos = gameplay_pos
        self.camera_selection_pos = camera_selection_pos
        self.manual_camera_coords = manual_camera_coords

    @abstractmethod
    def apply(self, original_clip):
        """
        Recibe: clip original.
        Retorna: clip procesado.
        """
        pass
