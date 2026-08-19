"""Phase 11a — continuous-talking facecam reset research.

RESEARCH ONLY, NOT SHIPPED. The supported product is the facecam MVP frozen in Phase 10;
nothing in this package is imported by `davinci_auto_zoom`, and the dependency only ever
points this way (research -> product). See `.agent/DECISIONS.md` D069.

The question under study is narrow: in a video where the creator talks almost continuously
and cuts most pauses out, **what decides that a hard cut deserves a return to X0, and when
does FACE_X1 start again?** The X1/X2/X3 dynamics are frozen and out of scope.
"""
