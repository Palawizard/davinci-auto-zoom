from __future__ import annotations

from collections.abc import Iterable

from davinci_auto_zoom.domain.models import SpeechSegment, ZoomAction, ZoomActionKind, ZoomState


def plan_basic_facecam_zoom(speech_segments: Iterable[SpeechSegment]) -> list[ZoomAction]:
    """Create a deliberately simple, Resolve-independent MVP action plan.

    This is not yet the final timing algorithm. It exists to establish the key architectural
    invariant: planning is pure and can be tested without DaVinci Resolve.
    """

    actions: list[ZoomAction] = []
    for segment in sorted(speech_segments, key=lambda item: item.frames.start):
        actions.append(
            ZoomAction(
                frame=segment.frames.start,
                kind=ZoomActionKind.ENTER,
                target=ZoomState.FACECAM_X1,
                asset_role="facecam_x1",
                reason="speech_start",
            )
        )
        actions.append(
            ZoomAction(
                frame=segment.frames.end,
                kind=ZoomActionKind.RESET,
                target=ZoomState.NORMAL,
                asset_role="reset_x0",
                reason="speech_end",
            )
        )
    return actions
