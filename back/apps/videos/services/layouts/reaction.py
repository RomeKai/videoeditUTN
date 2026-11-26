from .gaming import GamingLayout


class ReactionLayout(GamingLayout):
    """
    Layout 'Reaction' (YouTube Shorts): the content on top and the face of
    whoever reacts below, half and half (CORE-03).

    Same panes as ``split`` in the other order: the content pane is the
    slice at ``gameplay_position`` and the face pane follows the face found
    in ``camera_selection_position`` (or the manual camera), so the
    "Avanzado" options of ``split`` work the same here.
    """
    CAMERA_SHARE = 0.5
    CAMERA_ON_TOP = False
