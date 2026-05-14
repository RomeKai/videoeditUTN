from .standard import FillLayout, FitLayout
from .blur_pip import BlurredLayout
from .gaming import GamingLayout
from .pro import VersusLayout, ActiveSpeakerLayout

LAYOUT_REGISTRY = {
    'fill': FillLayout,
    'fit': FitLayout,
    'blurred': BlurredLayout,
    'split': GamingLayout,
    'pip': FitLayout, 
    'versus': VersusLayout,
    'active': ActiveSpeakerLayout,
}

def get_layout_strategy(layout_name, target_w, target_h, use_facetracking=False, gameplay_pos='center'):
    strategy_class = LAYOUT_REGISTRY.get(layout_name, FillLayout)
    return strategy_class(target_w, target_h, use_facetracking=use_facetracking, gameplay_pos=gameplay_pos)