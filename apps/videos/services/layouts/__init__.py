from .standard import FillLayout, FitLayout
from .blur_pip import BlurredLayout
from .gaming import SplitLayout

LAYOUT_REGISTRY = {
    'fill': FillLayout,
    'fit': FitLayout,
    'blurred': BlurredLayout,
    'split': SplitLayout,
    'pip': FitLayout, 
}

def get_layout_strategy(layout_name, target_w, target_h):
    strategy_class = LAYOUT_REGISTRY.get(layout_name, FillLayout)
    return strategy_class(target_w, target_h)