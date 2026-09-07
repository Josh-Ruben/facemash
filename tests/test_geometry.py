"""Pure-logic tests for the face-touch geometry (no camera / MediaPipe needed).

Run with:  python -m tests.test_geometry   (or pytest)
"""

from facemash.detector import (
    Detector,
    ALL_HAND_POINTS,
    FINGERTIPS,
    SENSITIVITY_PROFILES,
    BBox,
    hands_touch_face,
)


def _hand_with_tip(x, y, idx=8):
    """A 21-point hand where only landmark ``idx`` (default index tip) is at (x, y)."""
    pts = [(-1.0, -1.0)] * 21
    pts[idx] = (x, y)
    return pts


def run():
    face = BBox(0.4, 0.3, 0.2, 0.3)  # center-ish face, spans x[0.4,0.6] y[0.3,0.6]

    # 1. fingertip clearly inside the face -> touch
    assert hands_touch_face(face, [_hand_with_tip(0.5, 0.45)], 0.0, FINGERTIPS)

    # 2. fingertip far away -> no touch
    assert not hands_touch_face(face, [_hand_with_tip(0.05, 0.95)], 0.0, FINGERTIPS)

    # 3. no face -> never a touch
    assert not hands_touch_face(None, [_hand_with_tip(0.5, 0.45)], 0.2, ALL_HAND_POINTS)

    # 4. positive margin grows the region to include a point just outside it
    near = _hand_with_tip(0.4 - 0.01, 0.45)  # just left of the box
    assert not hands_touch_face(face, [near], 0.0, FINGERTIPS)
    assert hands_touch_face(face, [near], 0.2, FINGERTIPS)

    # 5. NEGATIVE margin (Low sensitivity) shrinks the region: a point near the
    #    edge that was inside the raw box is no longer counted.
    edge = _hand_with_tip(0.41, 0.45)  # just inside the left edge
    assert hands_touch_face(face, [edge], 0.0, FINGERTIPS)
    assert not hands_touch_face(face, [edge], -0.10, FINGERTIPS)

    # 6. bottom_limit excludes points below it (chin / jaw / beard)
    chin = _hand_with_tip(0.5, 0.58)  # low in the face box
    assert hands_touch_face(face, [chin], 0.0, FINGERTIPS)               # no limit -> touch
    assert not hands_touch_face(face, [chin], 0.0, FINGERTIPS, bottom_limit=0.50)  # below line -> ignored
    cheek = _hand_with_tip(0.5, 0.40)  # above the line
    assert hands_touch_face(face, [cheek], 0.0, FINGERTIPS, bottom_limit=0.50)

    # 7. negative-margin expansion clamps width/height to >= 0
    tiny = BBox(0.5, 0.5, 0.05, 0.05).expanded(-1.0)
    assert tiny.w >= 0.0 and tiny.h >= 0.0
    assert not hands_touch_face(BBox(0.5, 0.5, 0.05, 0.05), [_hand_with_tip(0.5, 0.5)], -1.0, FINGERTIPS)

    # 8. each sensitivity preset is well-formed (signed margin + point indices)
    for name, (margin, idx) in SENSITIVITY_PROFILES.items():
        assert -1.0 < margin < 1.0
        assert all(0 <= i < 21 for i in idx)

    # 9. empty hand list -> no touch
    assert not hands_touch_face(face, [], 0.2, ALL_HAND_POINTS)

    print("all geometry tests passed")


if __name__ == "__main__":
    run()
