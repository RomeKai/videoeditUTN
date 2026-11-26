from .standard import FillLayout, FitLayout
from .blur_pip import BlurredLayout
from .gaming import GamingLayout
from .pro import VersusLayout, ActiveSpeakerLayout
from .reaction import ReactionLayout
from .cinematic import CinematicLayout
from .pip import PipLayout
from .podcast import PodcastLayout

LAYOUT_REGISTRY = {
    'fill': FillLayout,
    'fit': FitLayout,
    'blurred': BlurredLayout,
    'split': GamingLayout,
    'pip': PipLayout,
    'versus': VersusLayout,
    'active': ActiveSpeakerLayout,
    'reaction': ReactionLayout,
    'cinematic': CinematicLayout,
    'podcast': PodcastLayout,
}

def get_layout_strategy(layout_name, target_w, target_h, use_facetracking=False, gameplay_pos='center', camera_selection_pos='center', manual_camera_coords=None, editing_style=None, static=False):
    strategy_class = LAYOUT_REGISTRY.get(layout_name, FillLayout)
    return strategy_class(target_w, target_h, use_facetracking=use_facetracking, gameplay_pos=gameplay_pos, camera_selection_pos=camera_selection_pos, manual_camera_coords=manual_camera_coords, editing_style=editing_style, static=static)
