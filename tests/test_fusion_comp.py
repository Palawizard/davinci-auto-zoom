"""Tests for the exported-Fusion-composition comparison.

The fixtures below are trimmed copies of comps really exported from the user's project on
Resolve Studio 21.0.4.5, keeping the parts that decide the question: the tool graph, the
wiring, the keyframes, and the length-dependent fields.
"""

from __future__ import annotations

from davinci_auto_zoom.domain.fusion_comp import compare_comps, parse_comp

X1_87_FRAMES = """Composition {
\tCurrentTime = 0,
\tRenderRange = { 0, 86 },
\tCurrentID = 17,
\tVersion = "DaVinci Resolve Studio 21.0.4.0005",
\tTools = {
\t\tMediaOut1 = Saver {
\t\t\tInputs = {
\t\t\t\tInput = Input {
\t\t\t\t\tSourceOp = "Transform1",
\t\t\t\t}
\t\t\t},
\t\t},
\t\tMediaIn1 = Loader {
\t\t\tCustomData = { MediaProps = {
\t\t\t\t\tMEDIA_NAME = "FACE_X1",
\t\t\t\t\tMEDIA_NUM_FRAMES = 87,
\t\t\t\t}, },
\t\t},
\t\tTransform1Size = BezierSpline {
\t\t\tKeyFrames = {
\t\t\t\t[0] = { 1, RH = { 5, 1 } },
\t\t\t\t[15] = { 1.5, LH = { -0.15151515151515, 1.5 } }
\t\t\t}
\t\t},
\t\tTransform1 = Transform {
\t\t\tInputs = {
\t\t\t\tSize = Input {
\t\t\t\t\tSourceOp = "Transform1Size",
\t\t\t\t}
\t\t\t},
\t\t}
\t},
}
"""

# Same effect, different clip length and session identifiers.
X1_141_FRAMES = (
    X1_87_FRAMES.replace("RenderRange = { 0, 86 }", "RenderRange = { 0, 140 }")
    .replace("MEDIA_NUM_FRAMES = 87", "MEDIA_NUM_FRAMES = 141")
    .replace("CurrentID = 17", "CurrentID = 42")
    .replace("21.0.4.0005", "21.0.4.9999")
)

# A different asset: identical graph, inverted zoom keyframes.
X0_42_FRAMES = (
    X1_87_FRAMES.replace('MEDIA_NAME = "FACE_X1"', 'MEDIA_NAME = "FACE_X0_SMOOTH"')
    .replace("[0] = { 1, RH = { 5, 1 } }", "[0] = { 1.5, RH = { 5, 1.5 } }")
    .replace("[15] = { 1.5, LH = { -0.15151515151515, 1.5 } }", "[15] = { 1, LH = { -0.1, 1 } }")
)

# What a freshly created, un-configured generator would look like.
EMPTY_COMP = """Composition {
\tTools = {
\t\tMediaOut1 = Saver {
\t\t\tInputs = {
\t\t\t\tInput = Input {
\t\t\t\t\tSourceOp = "MediaIn1",
\t\t\t\t}
\t\t\t},
\t\t},
\t\tMediaIn1 = Loader {
\t\t},
\t},
}
"""


def test_parse_extracts_tools_links_and_keyframes() -> None:
    fingerprint = parse_comp(X1_87_FRAMES)
    assert fingerprint.media_name == "FACE_X1"
    assert dict(fingerprint.tools) == {
        "MediaOut1": "Saver",
        "MediaIn1": "Loader",
        "Transform1Size": "BezierSpline",
        "Transform1": "Transform",
    }
    assert ("MediaOut1", "Transform1") in fingerprint.links
    assert fingerprint.keyframe_times == (("Transform1Size", (0.0, 15.0)),)


def test_identical_comps_are_identical() -> None:
    comparison = compare_comps(X1_87_FRAMES, X1_87_FRAMES)
    assert comparison.verdict == "identical"
    assert comparison.carries_user_effect is True


def test_same_effect_at_a_different_length_is_equivalent_modulo_duration() -> None:
    comparison = compare_comps(X1_87_FRAMES, X1_141_FRAMES)
    assert comparison.verdict == "equivalent-modulo-duration"
    assert comparison.carries_user_effect is True
    # Keyframes stay where they were: the animation is not rescaled by the new length.
    assert comparison.keyframe_times_equal is True
    assert comparison.duration_dependent_differences
    assert comparison.unexplained_differences == ()


def test_the_extent_flag_of_a_resized_instance_is_not_an_unexplained_difference() -> None:
    """Resolve marks an explicitly-sized clip with `ExtentSet`; it is not the effect."""

    resized = X1_141_FRAMES.replace(
        "\t\tMediaIn1 = Loader {\n", "\t\tMediaIn1 = Loader {\n\t\t\tExtentSet = true,\n"
    )
    comparison = compare_comps(X1_87_FRAMES, resized)
    assert comparison.unexplained_differences == ()
    assert comparison.verdict == "equivalent-modulo-duration"


def test_a_different_asset_is_reported_as_different() -> None:
    comparison = compare_comps(X1_87_FRAMES, X0_42_FRAMES)
    assert comparison.verdict == "different"
    assert comparison.carries_user_effect is False
    assert comparison.tools_equal is True  # same graph shape...
    assert comparison.keyframes_equal is False  # ...but not the same animation
    assert comparison.media_names == ("FACE_X1", "FACE_X0_SMOOTH")


def test_an_empty_generic_comp_does_not_pass_as_the_user_effect() -> None:
    comparison = compare_comps(X1_87_FRAMES, EMPTY_COMP)
    assert comparison.verdict == "different"
    assert comparison.carries_user_effect is False
    assert comparison.keyframes_equal is False


def test_unparsable_input_is_never_reported_as_a_match() -> None:
    comparison = compare_comps(X1_87_FRAMES, "")
    assert comparison.verdict == "unparsable"
    assert comparison.carries_user_effect is False
