"""Phase 11b — sealed blind validation of continuous-talking facecam resets.

RESEARCH ONLY, NOT SHIPPED, and it changes nothing about the Phase 10 facecam product. It
reuses the Phase 11a primitives (`structure`, `manual`, `reference`, `words`, `lexical`,
`acoustic`, `ablation`) and adds exactly three things Phase 11a did not have:

    simulate.py     a causal, label-free simulation of the face state inside one Short, so a
                    reset policy can be asked "which state are we in?" without ever consulting
                    the creator's own zoom track;
    policy.py       the frozen reset candidates P0-P3, their rhythm rule and their re-entry
                    rule, plus the blind prediction record that is committed before the labels
                    are looked at;
    evaluate.py     strict and subset scoring of those predictions against the manual edit,
                    including the exact-frame deltas that separate "wrong decision" from
                    "right decision, wrong frame".

Nothing here is imported by `davinci_auto_zoom`, and nothing here writes to a timeline.
"""
