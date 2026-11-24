from abc import ABC, abstractmethod

class BaseLayout(ABC):
    """
    Interfaz abstracta para estrategias de layout.
    """
    
    def __init__(self, target_w, target_h, use_facetracking=False, gameplay_pos='center', camera_selection_pos='center', manual_camera_coords=None, editing_style=None, static=False):
        self.target_w = int(target_w)
        self.target_h = int(target_h)
        # static: no face or speaker tracking at all, whatever the layout
        # (previews). use_facetracking alone does not stop the 'active' layout.
        self.static = static
        self.use_facetracking = use_facetracking and not static
        self.gameplay_pos = gameplay_pos
        self.camera_selection_pos = camera_selection_pos
        self.manual_camera_coords = manual_camera_coords
        self.editing_style = editing_style

    @property
    def tracking_fps(self):
        """Face sampling rate for this edit (CORE-01: faster styles sample more often)."""
        from apps.videos.services.face_tracking import sampling_fps

        return sampling_fps(self.editing_style)

    @abstractmethod
    def apply(self, original_clip):
        """
        Recibe: clip original.
        Retorna: clip procesado.
        """
        pass
