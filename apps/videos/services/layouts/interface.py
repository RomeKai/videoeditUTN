from abc import ABC, abstractmethod

class BaseLayout(ABC):
    """
    Interfaz abstracta para estrategias de layout.
    """
    
    def __init__(self, target_w, target_h, use_facetracking=False):
        self.target_w = int(target_w)
        self.target_h = int(target_h)
        self.use_facetracking = use_facetracking

    @abstractmethod
    def apply(self, original_clip):
        """
        Recibe: clip original.
        Retorna: clip procesado.
        """
        pass