import pytest

from davinci_auto_zoom.resolve.docs import parse_scripting_readme

README_EXCERPT = """\
Overview
--------
Some prose that mentions Foo() --> Bar but is not indented as an API line.

DaVinci Resolve API
-------------------
Resolve
  GetVersionString()                              --> string             # Returns product version.
  GetProjectManager()                             --> ProjectManager     # Project manager.

Timeline
  GetTrackCount(trackType)                        --> int                # Number of tracks.
  AddTrack(trackType, subTrackType)               --> Bool               # Adds a track.
  AddTrack(trackType, newTrackOptions)            --> Bool               # Overload.
  AddMarker(frameId, color, name, note, duration, --> Bool               # Wrapped signature.
            customData)

Deprecated Resolve API Functions
--------------------------------
Timeline
  GetItemsInTrack(trackType, index)               --> {items...}         # Deprecated.
"""


def test_parses_classes_and_methods() -> None:
    api = parse_scripting_readme(README_EXCERPT)

    assert set(api) == {"Resolve", "Timeline"}
    assert api["Resolve"]["GetVersionString"].returns == "string"
    assert api["Timeline"]["GetTrackCount"].args == "trackType"


def test_first_overload_wins() -> None:
    api = parse_scripting_readme(README_EXCERPT)
    assert api["Timeline"]["AddTrack"].args == "trackType, subTrackType"


def test_deprecated_section_is_excluded() -> None:
    api = parse_scripting_readme(README_EXCERPT)
    assert "GetItemsInTrack" not in api["Timeline"]


def test_section_titles_are_not_treated_as_classes() -> None:
    api = parse_scripting_readme(README_EXCERPT)
    assert "Overview" not in api


def test_signature_is_readable() -> None:
    api = parse_scripting_readme(README_EXCERPT)
    assert api["Timeline"]["GetTrackCount"].signature == "Timeline.GetTrackCount(trackType) --> int"


def test_signature_wrapped_across_two_lines_is_still_parsed() -> None:
    api = parse_scripting_readme(README_EXCERPT)
    assert api["Timeline"]["AddMarker"].returns == "Bool"


def test_installed_readme_is_parsed_when_present() -> None:
    """Guards against the parser silently regressing on the real installed document."""
    from davinci_auto_zoom.resolve.docs import load_documented_api

    path, api = load_documented_api()
    if path is None:
        pytest.skip("Resolve Developer/Scripting documentation is not installed")
    assert {"Resolve", "Project", "Timeline", "TimelineItem", "MediaPool"} <= set(api)
    assert "GetItemListInTrack" in api["Timeline"]
