from davinci_auto_zoom.domain.models import FrameRange, SpeechSegment, ZoomActionKind, ZoomState
from davinci_auto_zoom.domain.planner import plan_basic_facecam_zoom


def test_basic_planner_emits_enter_and_reset() -> None:
    actions = plan_basic_facecam_zoom([SpeechSegment(FrameRange(100, 220))])

    assert [(action.frame, action.kind, action.target) for action in actions] == [
        (100, ZoomActionKind.ENTER, ZoomState.FACECAM_X1),
        (220, ZoomActionKind.RESET, ZoomState.NORMAL),
    ]
