import math
import random

import pytest

from rotary.gestures import GestureEngine, GestureSettings, HandednessTracker, Phase, extract_features
from tests.synth import make_hand

FPS = 60.0


def feats(**kw):
    return extract_features(make_hand(**kw))


# ----------------------------------------------------------------- features
@pytest.mark.parametrize("roll", [-90, -45, 0, 30, 75, 120])
@pytest.mark.parametrize("handed", ["Right", "Left"])
def test_palm_vs_back_any_roll(roll, handed):
    assert feats(roll_deg=roll, handed=handed).facing > 0.3
    assert feats(roll_deg=roll, handed=handed, palm=False).facing < -0.3
    assert feats(roll_deg=roll, handed=handed, flex=1.0).facing > 0.3


def test_flip_handedness_inverts_facing():
    hand = make_hand()
    assert extract_features(hand, flip_handedness=True).facing < -0.3


def test_grip_open_relaxed_fist():
    assert feats(flex=0.0).grip < 0.2
    assert feats(flex=0.3).grip < 0.45
    assert feats(flex=1.0).grip > 0.75
    assert feats(flex=1.0, roll_deg=60).grip > 0.75


@pytest.mark.parametrize("handed", ["Right", "Left"])
@pytest.mark.parametrize("flex", [0.0, 1.0])
def test_roll_angle_tracks_hand(handed, flex):
    base = feats(handed=handed, flex=flex).angle
    for roll in (-60, -20, 25, 70):
        got = math.degrees(math.atan2(math.sin(feats(handed=handed, flex=flex, roll_deg=roll).angle - base),
                                      math.cos(feats(handed=handed, flex=flex, roll_deg=roll).angle - base)))
        assert abs(got - roll) < 3.0, (roll, got)


def test_pinch():
    assert feats(pinch=True).pinch < 0.3
    assert feats().pinch > 0.45


def test_pinch_survives_a_misjudged_depth():
    """MediaPipe guesses depth: fingertips touching on screen can sit 3 cm apart in its 3D skeleton."""
    h = make_hand(pinch=True)
    h.world[4] = h.world[8] + (0.008, 0.008, 0.028)
    assert extract_features(h).pinch < GestureSettings().pinch_on
    assert feats(flex=0.3).pinch > GestureSettings().pinch_off      # a relaxed open hand isn't pinching


def test_relaxed_pinch_executes():
    """A pinch reading 0.33 palm lengths (the old 0.30 threshold missed it), the other fingers half curled."""
    s = Sim(turn_style="direct")
    s.run(3.1)
    s.turn(0, -22, seconds=0.3)
    ev = s.run(2 / FPS, roll_deg=-22, finger_flex=(0.0, 0.45, 0.45, 0.45), feat={"pinch": 0.33})
    assert "execute" in s.names(ev)


def test_pinch_while_the_palm_tips_away_still_clicks():
    s = Sim(turn_style="direct")
    s.run(3.1)
    s.turn(0, -22, seconds=0.3)
    assert s.run(2 / FPS, roll_deg=-22, feat={"pinch": 0.2, "facing": -0.3}) == []     # palm tipped away
    assert s.e.pinching
    ev = s.run(0.3, roll_deg=-22, feat={"pinch": 0.2})
    assert "execute" in s.names(ev)


# ------------------------------------------------------------------ engine
class Sim:
    def __init__(self, n=8, **settings):
        self.e = GestureEngine(n, GestureSettings(**settings))
        self.t = 0.0
        self.events = []
        self.rng = random.Random(1)

    def run(self, seconds, jitter=0.002, **hand):
        """Feed frames; hand=None-like via absent=True; feat={...} overrides features."""
        absent = hand.pop("absent", False)
        feat = hand.pop("feat", {})
        out = []
        for _ in range(int(round(seconds * FPS))):
            self.t += 1 / FPS
            if absent:
                f = None
            else:
                c = hand.get("center", (0.5, 0.5))
                h = dict(hand, center=(c[0] + self.rng.uniform(-jitter, jitter),
                                       c[1] + self.rng.uniform(-jitter, jitter)))
                f = extract_features(make_hand(**h))
                for k, v in feat.items():
                    setattr(f, k, v)
            ev = self.e.update(f, self.t)
            out += [(round(self.t, 3), *e) for e in ev]
        self.events += out
        return out

    def turn(self, start, end, seconds=0.5, **hand):
        n = int(seconds * FPS)
        out = []
        for i in range(1, n + 1):
            out += self.run(1 / FPS, roll_deg=start + (end - start) * i / n, **hand)
        return out

    def names(self, evs):
        return [e[1] for e in evs]


def test_stillness_opens_after_three_seconds():
    s = Sim()
    assert s.run(2.9) == []
    assert 0.9 < s.e.snapshot().arming < 1.0
    ev = s.run(0.2)
    assert s.names(ev) == ["open"] and ev[0][2] == "still"
    assert 2.95 <= ev[0][0] <= 3.05


def test_large_movement_prevents_opening():
    s = Sim()
    for i in range(12):  # wander back and forth by ~0.7 palm lengths every half second
        x = 0.5 + (0.1 if i % 2 else -0.1)
        assert s.run(0.5, center=(x, 0.5)) == []
    assert s.e.phase == Phase.IDLE


def test_back_of_hand_never_opens():
    s = Sim()
    assert s.run(4.0, palm=False) == []
    assert s.run(1.0, palm=False, flex=1.0) == []


def test_grip_opens_instantly():
    s = Sim()
    s.run(0.5)
    ev = s.run(0.2, flex=1.0)
    assert s.names(ev) == ["open"] and ev[0][2] == "grip"
    assert ev[0][0] - 0.5 < 0.15


def test_turning_selects_like_a_knob():
    s = Sim(turn_style="direct")
    s.run(3.1)
    assert s.e.phase == Phase.OPEN
    ev = s.turn(0, 44)            # clockwise by two detents
    assert [e[2] for e in ev if e[1] == "select"] == [7, 6]
    ev = s.turn(44, -44, seconds=0.8)
    assert s.e.selected == 2
    assert s.e.snapshot().position == pytest.approx(2.0, abs=0.3)


def test_grip_turn_then_point_executes():
    s = Sim(turn_style="direct")
    s.run(0.3)
    s.run(0.2, flex=1.0)
    assert s.e.phase == Phase.OPEN and s.e.snapshot().grip_mode
    s.turn(0, -66, flex=1.0)
    assert s.e.selected == 3
    assert s.e.snapshot().grip_turned
    ev = s.run(0.3, roll_deg=-66, finger_flex=(0, 1, 1, 1))     # index finger out
    assert ("execute", 3) in [(e[1], e[2]) for e in ev]
    ev = s.run(1.0, roll_deg=-66)
    assert "closed" in s.names(ev)
    assert s.e.phase == Phase.IDLE


def test_grip_release_without_turn_keeps_dial_open():
    s = Sim()
    s.run(0.3)
    s.run(0.3, flex=1.0)
    ev = s.run(0.5, flex=0.0)
    assert "execute" not in s.names(ev)
    assert s.e.phase == Phase.OPEN


def test_dwell_executes_after_turning():
    s = Sim(dwell_seconds=1.6, turn_style="direct")
    s.run(3.1)
    s.turn(0, 22, seconds=0.3)
    assert s.e.selected == 7
    ev = s.run(1.4, roll_deg=22)
    assert "execute" not in s.names(ev)
    assert s.e.snapshot().dwell > 0.7
    ev = s.run(0.4, roll_deg=22)
    assert ("execute", 7) in [(e[1], e[2]) for e in ev]


def test_no_dwell_before_any_turn():
    s = Sim()
    s.run(3.1)
    ev = s.run(5.0)
    assert "execute" not in s.names(ev)


def test_pinch_executes():
    s = Sim(turn_style="direct")
    s.run(3.1)
    s.turn(0, -22, seconds=0.3)
    ev = s.run(0.2, roll_deg=-22, pinch=True)
    assert ("execute", 1) in [(e[1], e[2]) for e in ev]


def test_hand_lost_closes():
    s = Sim()
    s.run(3.1)
    assert s.run(1.0, absent=True) == []
    ev = s.run(0.6, absent=True)
    assert ("close", "lost") in [(e[1], e[2]) for e in ev]


def test_back_of_hand_dismisses():
    s = Sim()
    s.run(3.1)
    ev = s.run(0.8, palm=False)
    assert ("close", "dismiss") in [(e[1], e[2]) for e in ev]


def test_rearm_after_close_requires_move_or_leaving():
    s = Sim()
    s.run(3.1)
    s.run(0.8, palm=False)               # dismiss
    assert s.e.phase == Phase.IDLE
    assert s.run(5.0) == []              # still palm, no re-open
    assert s.e.snapshot().rearm_needed
    s.run(0.3, center=(0.65, 0.5))       # large move re-arms
    ev = s.run(3.2, center=(0.65, 0.5))
    assert s.names(ev) == ["open"]


def test_hand_reacquired_does_not_jump_selection():
    s = Sim()
    s.run(3.1)
    s.turn(0, 22, seconds=0.3)
    sel = s.e.selected
    s.run(0.5, absent=True)
    s.run(0.5, roll_deg=-40)              # comes back at a different roll
    assert s.e.selected == sel


def test_selection_persists_between_openings():
    s = Sim(turn_style="direct")
    s.run(0.3)
    s.run(0.1, flex=1.0)
    s.turn(0, -44, flex=1.0)
    s.run(0.3, roll_deg=-44, finger_flex=(0, 1, 1, 1))   # point → execute item 2
    s.run(2.5, absent=True)
    s.run(0.3)
    s.run(0.2, flex=1.0)
    assert s.e.phase == Phase.OPEN and s.e.selected == 2


def test_many_items_scale_detent():
    e = GestureEngine(16, GestureSettings(degrees_per_item=22))
    assert e.step_degrees == pytest.approx(12.5)


def test_manual_open_waits_for_a_hand():
    s = Sim()
    s.e.force_open(s.t)
    assert s.run(3.0, absent=True) == []
    assert s.e.phase == Phase.OPEN
    s.run(0.3)
    ev = s.run(1.7, absent=True)
    assert ("close", "lost") in [(e[1], e[2]) for e in ev]


# ------------------------------------------------------------------ intent
def test_resting_fist_does_not_open():
    s = Sim()
    assert s.run(4.0, flex=1.0) == []           # fist arrives already closed, never shown open
    r = Sim(intent="relaxed")
    assert r.names(r.run(0.5, flex=1.0)) == ["open"]


def test_grab_must_follow_open_palm_quickly():
    s = Sim()
    s.run(0.4)                                   # open palm...
    s.run(2.0, flex=0.5)                         # ...then a relaxed half-curl for 2 s
    assert s.run(0.5, flex=1.0) == []            # too long since the palm was presented


def test_fist_while_moving_fast_is_ignored():
    s = Sim()
    s.run(0.4)
    evs = []
    for i in range(20):                           # swinging the arm while closing the hand
        evs += s.run(1 / 60, flex=1.0, center=(0.3 + 0.02 * i, 0.5))
    assert "open" not in s.names(evs)


def test_hand_at_face_never_opens():
    s = Sim()
    assert s.run(4.0, feat={"at_face": True}) == []
    s.run(0.3, feat={"at_face": True})
    assert s.run(0.5, flex=1.0, feat={"at_face": True}) == []
    s2 = Sim(ignore_near_face=False)
    assert s2.names(s2.run(3.2, feat={"at_face": True})) == ["open"]


def test_uncertain_handedness_never_opens():
    s = Sim()
    assert s.run(4.0, feat={"handed_conf": 0.3}) == []


def test_tilted_or_cut_off_hand_does_not_open_by_holding():
    assert Sim().run(4.0, roll_deg=80) == []
    assert Sim().run(4.0, feat={"in_frame": False}) == []
    assert Sim(intent="relaxed").names(Sim(intent="relaxed").run(3.2, roll_deg=70)) == ["open"]


def test_cooldown_after_close_blocks_instant_reopen():
    s = Sim()
    s.run(3.1)
    s.run(0.8, palm=False)                       # dismiss
    s.run(0.2)
    ev = s.run(0.3, flex=1.0)                    # grab right away: still cooling down
    assert "open" not in s.names(ev)
    s.run(1.0)
    ev = s.run(0.3, flex=1.0)
    assert "open" in s.names(ev)


def test_summon_switches():
    assert Sim(still_summon=False).run(4.0) == []
    s = Sim(grip_summon=False)
    s.run(0.4)
    assert s.run(0.5, flex=1.0) == []


def test_handedness_tracker_ignores_fist_flicker():
    h = HandednessTracker()
    t = 0.0
    for _ in range(20):
        t += 1 / 60
        label, conf = h.update("Left", 0.95, 0.0, t)
    assert label == "Left" and conf > 0.8
    for i in range(60):                           # fist frames flicker between labels
        t += 1 / 60
        label, conf = h.update("Right" if i % 2 else "Left", 0.9, 1.0, t)
    assert label == "Left"
    h2 = HandednessTracker()
    for i in range(30):                           # a fist that was never seen open stays uncertain
        label, conf = h2.update("Right" if i % 3 else "Left", 0.8, 1.0, i / 60)
    assert conf < 0.6


def test_pointing_is_not_a_fist():
    assert feats(finger_flex=(0, 1, 1, 1)).grip > 0.5      # three fingers curled, index straight
    s = Sim()
    s.run(0.4)
    assert s.run(1.0, finger_flex=(0, 1, 1, 1)) == []
    assert not s.e.gripping


# ------------------------------------------------------------------ fingers
POINT = dict(finger_flex=(0, 1, 1, 1))


def test_finger_flick_steps_one_item_each_way():
    s = Sim()
    s.run(3.1)
    s.run(0.5, **POINT)
    assert s.e.snapshot().pointing
    ev = []
    for x in [0.52, 0.56, 0.62, 0.68, 0.68, 0.62, 0.56, 0.52, 0.5]:     # flick right and back
        ev += s.run(1 / 60, center=(x, 0.5), **POINT)
    ev += s.run(0.6, **POINT)
    assert [e[2] for e in ev if e[1] == "select"] == [7]
    ev = []
    for x in [0.48, 0.44, 0.38, 0.32, 0.32, 0.38, 0.44, 0.48, 0.5]:     # and to the left
        ev += s.run(1 / 60, center=(x, 0.5), **POINT)
    ev += s.run(0.6, **POINT)
    assert [e[2] for e in ev if e[1] == "select"] == [0]


def test_finger_push_clicks():
    s = Sim()
    s.run(3.1)
    s.run(0.5, **POINT)
    ev = []
    for i in range(12):
        ev += s.run(1 / 60, index_push=6 * (i + 1), **POINT)
    assert ("execute", 0) in [(e[1], e[2]) for e in ev]


def test_slow_finger_drift_neither_swipes_nor_clicks():
    s = Sim()
    s.run(3.1)
    ev = s.run(0.5, **POINT)
    for i in range(60):
        ev += s.run(1 / 60, center=(0.5 + 0.001 * i, 0.5), index_push=0.2 * i, **POINT)
    assert not [e for e in ev if e[1] in ("select", "execute")]
    assert s.e.phase == Phase.OPEN


def test_finger_controls_can_be_disabled():
    s = Sim(finger_swipe=False, finger_push=False)
    s.run(3.1)
    s.run(0.5, **POINT)
    ev = []
    for i in range(9):
        ev += s.run(1 / 60, center=(0.5 + 0.02 * (i + 1), 0.5), **POINT)
    for i in range(12):
        ev += s.run(1 / 60, center=(0.68, 0.5), index_push=6 * (i + 1), **POINT)
    assert not [e for e in ev if e[1] in ("select", "execute")]



# ------------------------------------------------------------------ tilt & hold
def test_tilt_and_hold_keeps_scrolling_until_straight():
    s = Sim()                                    # tilt is the default style
    s.run(3.1)
    ev = s.turn(0, 30, seconds=0.25)             # lean clockwise past the threshold
    assert [e[2] for e in ev if e[1] == "select"] == [7]
    ev = s.run(1.6, roll_deg=30)                 # hold it: keeps going
    steps = [e[2] for e in ev if e[1] == "select"]
    assert len(steps) >= 3 and steps[:3] == [6, 5, 4]
    assert s.e.snapshot().scrolling == 1
    s.turn(30, 0, seconds=0.2)                   # straighten: stops
    ev = s.run(1.0)
    assert not [e for e in ev if e[1] == "select"]
    assert s.e.snapshot().scrolling == 0


def test_tilt_further_scrolls_faster():
    def count(deg):
        s = Sim(dwell_seconds=0)
        s.run(3.1)
        s.turn(0, deg, seconds=0.2)
        return len([e for e in s.run(2.0, roll_deg=deg) if e[1] == "select"])
    assert count(50) > count(26)


def test_tilt_counter_clockwise_goes_the_other_way():
    s = Sim()
    s.run(3.1)
    s.turn(0, -30, seconds=0.25)
    s.run(0.6, roll_deg=-30)
    assert s.e.selected in (1, 2)


def test_small_wobble_does_not_scroll():
    s = Sim()
    s.run(3.1)
    ev = []
    for i in range(120):
        ev += s.run(1 / 60, roll_deg=12 * math.sin(i / 10))
    assert not [e for e in ev if e[1] == "select"]


def test_grab_tilt_point_launches():
    s = Sim()
    s.run(0.3)
    s.run(0.2, flex=1.0)
    assert s.e.phase == Phase.OPEN
    s.turn(0, -30, seconds=0.2, flex=1.0)        # fist tilted counter-clockwise
    ev = s.run(0.1, roll_deg=-30, flex=1.0)
    sel = s.e.selected
    assert sel == 1
    ev = s.run(0.3, roll_deg=-30, flex=0.0)      # letting go doesn't launch
    assert "execute" not in s.names(ev)
    ev = s.run(0.3, roll_deg=-30, **POINT)       # pointing does
    assert ("execute", sel) in [(e[1], e[2]) for e in ev]


def test_click_while_scrolling_launches_current_item():
    s = Sim()
    s.run(3.1)
    s.turn(0, 30, seconds=0.25)
    s.run(0.7, roll_deg=30)
    ev = s.run(0.2, roll_deg=30, pinch=True)
    ex = [e for e in ev if e[1] == "execute"]
    assert ex and s.e.phase == Phase.EXECUTING


def test_dwell_after_tilting_back():
    s = Sim(dwell_seconds=1.6)
    s.run(3.1)
    s.turn(0, 30, seconds=0.2)
    s.turn(30, 0, seconds=0.2)
    sel = s.e.selected
    ev = s.run(1.8)
    assert ("execute", sel) in [(e[1], e[2]) for e in ev]


def test_finger_held_aside_keeps_scrolling():
    s = Sim()
    s.run(3.1)
    s.run(0.5, **POINT)
    ev = []
    for x in [0.54, 0.6, 0.66, 0.7]:
        ev += s.run(1 / 60, center=(x, 0.5), **POINT)
    ev += s.run(1.5, center=(0.7, 0.5), **POINT)   # hold the finger to the right
    steps = [e[2] for e in ev if e[1] == "select"]
    assert len(steps) >= 3 and steps[:3] == [7, 6, 5]
    for x in [0.62, 0.55, 0.5]:
        s.run(1 / 60, center=(x, 0.5), **POINT)
    ev = s.run(1.0, **POINT)                       # back to centre: stops
    assert not [e for e in ev if e[1] == "select"]



# ------------------------------------------------------------------ move style, either side, levels, keys
def test_move_style_scrolls_while_held_aside():
    s = Sim(turn_style="move", dwell_seconds=0)
    s.run(3.1)
    ev = []
    for i in range(10):                            # slide right by ~1.3 palm lengths
        ev += s.run(1 / 60, center=(0.5 + 0.03 * (i + 1), 0.5))
    ev += s.run(1.2, center=(0.8, 0.5))            # hold it there
    steps = [e[2] for e in ev if e[1] == "select"]
    assert len(steps) >= 3 and steps[0] == 7       # right = the ring turns clockwise, like dragging it
    ev = []
    for i in range(10):
        ev += s.run(1 / 60, center=(0.8 - 0.03 * (i + 1), 0.5))
    ev2 = s.run(1.0)                               # back in the middle: stops
    assert not [e for e in ev2 if e[1] == "select"]


def test_move_style_ignores_wrist_roll():
    s = Sim(turn_style="move", dwell_seconds=0)
    s.run(3.1)
    ev = s.turn(0, 45, seconds=0.3) + s.run(1.0, roll_deg=45)
    assert not [e for e in ev if e[1] == "select"]


def test_either_side_lets_the_back_of_the_hand_work():
    s = Sim(either_side=True)
    ev = s.run(3.2, palm=False)
    assert s.names(ev) == ["open"]
    assert s.run(1.0, palm=False) == []            # and it doesn't dismiss itself
    assert Sim().run(3.2, palm=False) == []


def test_enter_level_starts_a_fresh_ring():
    s = Sim()
    s.run(3.1)
    s.e.enter_level(5, s.t, selected=1)
    snap = s.e.snapshot()
    assert snap.phase == Phase.OPEN and snap.selected == 1 and s.e.n == 5 and not snap.interacted
    ev = s.turn(0, 30, seconds=0.25) + s.run(0.2, roll_deg=30)
    assert [e[2] for e in ev if e[1] == "select"][0] == 0


def test_keyboard_steps_and_launches():
    s = Sim()
    s.e.force_open(s.t)
    assert s.e.key_step(1, s.t) == [("select", 1)]
    assert s.e.key_step(-2, s.t) == [("select", 7)]
    assert s.e.key_execute(s.t) == [("execute", 7)]
    assert s.e.key_step(1, s.t) == []              # not open any more


def test_idle_timeout_zero_means_never_close():
    s = Sim(idle_timeout=0)
    s.e.force_open(s.t)
    assert s.run(30.0, absent=True) == []
    assert s.e.phase == Phase.OPEN


def test_no_rearm_hint_when_hold_to_open_is_off():
    s = Sim(still_summon=False)
    s.run(0.3)
    s.run(0.2, flex=1.0)                           # grab opens
    s.run(0.8, palm=False)                         # dismiss
    s.run(1.0)
    assert not s.e.snapshot().rearm_needed


# ------------------------------------------------------------------ grip mode: a heavy knob held in the hand
def grabbed(**settings):
    s = Sim(**settings)
    s.run(0.3)
    s.run(0.2, flex=1.0)
    assert s.e.phase == Phase.OPEN and s.e.snapshot().grip_mode
    return s


def test_grip_tilt_turns_one_click_per_item_in_any_turn_style():
    for style in ("tilt", "move", "direct"):
        s = grabbed(turn_style=style)
        ev = s.turn(0, 46, seconds=0.5, flex=1.0) + s.run(0.5, roll_deg=46, flex=1.0)
        assert [e[2] for e in ev if e[1] == "select"] == [7, 6], style   # held still: stays put
        ev = s.turn(46, 0, seconds=0.5, flex=1.0)
        assert [e[2] for e in ev if e[1] == "select"] == [7, 0], style


def test_grip_needs_force_to_click_over():
    s = grabbed()
    ev = s.turn(0, 15, seconds=0.3, flex=1.0) + s.run(0.5, roll_deg=15, flex=1.0)   # past halfway, not past the detent
    assert not [e for e in ev if e[1] == "select"]
    assert -0.35 < s.e.position < -0.05                           # strains toward the hand
    ev = []
    for i in range(180):                                           # wobble around a detent
        ev += s.run(1 / 60, roll_deg=24 + 8 * math.sin(i / 6), flex=1.0)
    assert len([e for e in ev if e[1] == "select"]) == 1


def test_open_and_regrip_ratchets_without_moving():
    s = grabbed()
    s.turn(0, 46, seconds=0.4, flex=1.0)
    assert s.e.selected == 6
    s.turn(46, 20, seconds=0.2, flex=0.0)          # open the hand and turn back while it's open
    ev = s.run(0.4, roll_deg=-10, flex=0.0)
    ev += s.run(0.3, roll_deg=-10, flex=1.0)       # close it again somewhere else
    assert not [e for e in ev if e[1] in ("select", "execute")]
    assert s.e.selected == 6 and s.e.snapshot().grip_mode
    ev = s.turn(-10, 36, seconds=0.4, flex=1.0)    # and keep turning from there
    assert [e[2] for e in ev if e[1] == "select"] == [5, 4]


def test_open_hand_for_long_switches_to_palm_mode():
    s = grabbed(dwell_seconds=0)
    s.turn(0, 23, seconds=0.3, flex=1.0)
    assert s.e.selected == 7
    s.run(1.3, roll_deg=23)
    assert s.e.snapshot().grip_mode
    s.run(0.4, roll_deg=23)
    assert not s.e.snapshot().grip_mode and s.e.phase == Phase.OPEN
    ev = s.turn(23, 53, seconds=0.25) + s.run(1.0, roll_deg=53)   # palm mode: tilt and hold scrolls
    assert len([e for e in ev if e[1] == "select"]) >= 2


def test_grip_mode_ignores_pinch_and_dwell():
    s = grabbed(dwell_seconds=0.5)
    s.turn(0, 23, seconds=0.3, flex=1.0)
    ev = s.run(1.0, roll_deg=23, flex=1.0) + s.run(0.6, roll_deg=23, pinch=True)
    assert "execute" not in s.names(ev)


def test_palm_then_grip_enters_grip_mode():
    s = Sim()
    s.run(3.1)
    assert not s.e.snapshot().grip_mode
    s.run(0.2, flex=1.0)
    assert s.e.snapshot().grip_mode
    ev = s.turn(0, 23, seconds=0.3, flex=1.0) + s.run(1.0, roll_deg=23, flex=1.0)
    assert [e[2] for e in ev if e[1] == "select"] == [7]


def test_point_on_a_new_ring_needs_a_fresh_point():
    s = grabbed()
    s.run(0.3, **POINT)
    assert s.e.phase == Phase.EXECUTING
    s.e.enter_level(5, s.t)                        # opened a Quick nav ring
    ev = s.run(0.5, **POINT)                       # still pointing: doesn't open again
    assert "execute" not in s.names(ev)
    s.run(0.2, flex=1.0)
    ev = s.run(0.3, **POINT)
    assert ("execute", 0) in [(e[1], e[2]) for e in ev]


# ------------------------------------------------------------------ volume slider
TWO = dict(finger_flex=(0, 0, 1, 1))


def slide(s, y0, y1, seconds=0.6, **hand):
    n = int(seconds * FPS)
    out = []
    for i in range(1, n + 1):
        out += s.run(1 / FPS, center=(0.5, y0 + (y1 - y0) * i / n), jitter=0.0005, **hand)
    return out


def volume_steps(evs):
    return sum(e[2] for e in evs if e[1] == "volume")


def test_two_fingers_are_not_pointing_or_a_fist():
    s = Sim()
    s.run(0.4)
    s.run(0.6, **TWO)
    assert s.e.two_up and not s.e.pointing and not s.e.gripping


def test_two_finger_slide_changes_volume_with_the_rotary_closed():
    s = Sim()
    s.run(0.4, center=(0.5, 0.6), **TWO)
    up = slide(s, 0.6, 0.45, **TWO)                # up the screen: louder
    assert volume_steps(up) > 3
    assert s.e.snapshot().sliding
    down = slide(s, 0.45, 0.6, **TWO)
    assert volume_steps(down) < -3
    assert s.e.phase == Phase.IDLE                 # sliding never opens the rotary
    assert "open" not in s.names(s.events)


def test_holding_two_fingers_still_does_nothing():
    s = Sim()
    assert volume_steps(s.run(2.0, center=(0.5, 0.6), **TWO)) == 0


def test_volume_slide_works_with_the_rotary_open_and_holds_the_ring():
    s = Sim()
    s.run(3.1)
    assert s.e.phase == Phase.OPEN
    sel = s.e.selected
    s.run(0.4, center=(0.5, 0.6), **TWO)
    ev = slide(s, 0.6, 0.45, roll_deg=30, **TWO)    # tilted too: the ring must not scroll meanwhile
    assert volume_steps(ev) > 3
    assert "select" not in s.names(ev)
    assert s.e.selected == sel and s.e.phase == Phase.OPEN


def test_volume_slide_can_be_switched_off():
    s = Sim(volume_slide=False)
    s.run(0.4, center=(0.5, 0.6), **TWO)
    assert volume_steps(slide(s, 0.6, 0.45, **TWO)) == 0


# ------------------------------------------------------------------ slides / video rings
def test_limited_ring_stops_at_its_ends():
    s = Sim(n=8)
    s.run(3.1)
    s.e.enter_level(5, s.t, selected=0, limit=(0, 4))
    assert s.e.key_step(-1, s.t) == []             # already on the first slide
    for _ in range(10):
        s.e.key_step(1, s.t)
    assert s.e.sel_raw == 4


def test_limited_grip_turn_does_not_wrap():
    s = Sim(n=8)
    s.run(3.1)
    s.e.enter_level(4, s.t, selected=3, limit=(0, 3), fixed_step=26.0)
    s.run(0.3, flex=1.0)
    s.turn(0, -120, seconds=1.0, flex=1.0)          # counter-clockwise: towards later slides, past the end
    assert s.e.sel_raw == 3


def test_fixed_step_ignores_item_count():
    e = GestureEngine(60)
    assert e.step_degrees < 5
    e.fixed_step = 26.0
    assert e.step_degrees == 26.0


def test_set_position_moves_without_counting_as_a_turn():
    s = Sim(n=8)
    s.run(3.1)
    s.e.enter_level(120, s.t, selected=0, limit=(0, 120))
    s.e.set_position(130.0, s.t)
    assert s.e.sel_raw == 120 and not s.e.snapshot().interacted
    s.e.set_position(42.4, s.t)
    assert s.e.position == pytest.approx(42.4) and s.e.sel_raw == 42


# ------------------------------------------------------------------ short glitches, and a held fist turning on
def test_palm_hold_survives_a_moment_at_the_face():
    """Moving the hand out from in front of the face can flicker "touching the face" (or drop a frame or two)
    for a moment: the hold pauses and carries on instead of starting over."""
    s = Sim()
    s.run(2.0)
    s.run(0.1, feat={"at_face": True})
    assert s.e.snapshot().arming > 0.6
    s.run(0.15, absent=True)
    ev = s.run(1.2)
    assert s.names(ev) == ["open"] and ev[0][2] == "still"
    assert ev[0][0] < 3.4                          # 3 s of holding, plus the gaps


def test_long_time_at_the_face_still_starts_the_hold_over():
    s = Sim()
    s.run(2.0)
    s.run(0.8, feat={"at_face": True})
    assert s.e.snapshot().arming == 0.0
    assert s.run(2.5) == []


def test_grab_survives_a_one_frame_glitch():
    s = Sim()
    s.run(0.4)
    s.run(1 / 60, flex=1.0)
    s.run(2 / 60, flex=1.0, feat={"at_face": True})     # the face test flickers as the fist closes
    ev = s.run(0.2, flex=1.0)
    assert s.names(ev) == ["open"] and ev[0][2] == "grip"


def test_held_fist_turns_on_by_itself_and_stops_when_it_comes_back():
    s = grabbed()
    s.turn(0, 40, seconds=0.3, flex=1.0)              # turned well past the detents, then held there
    s.run(0.25, roll_deg=40, flex=1.0)
    sel = s.e.sel_raw
    ev = s.run(2.0, roll_deg=40, flex=1.0)
    steps = [e for e in ev if e[1] == "select"]
    assert len(steps) >= 3
    assert s.e.sel_raw < sel                          # on in the same direction as the turn
    stopped = s.e.sel_raw
    ev = s.turn(40, 0, seconds=0.4, flex=1.0) + s.run(1.0, roll_deg=0, flex=1.0)
    assert not [e for e in ev if e[1] == "select"]    # coming back stops it without clicking back
    assert s.e.sel_raw == stopped
    ev = s.turn(0, 23, seconds=0.3, flex=1.0)         # and the knob follows the hand again
    assert len([e for e in ev if e[1] == "select"]) == 1


def test_held_fist_auto_turn_can_be_switched_off():
    s = grabbed(grip_auto_seconds=0)
    s.turn(0, 40, seconds=0.3, flex=1.0)
    s.run(0.25, roll_deg=40, flex=1.0)
    sel = s.e.sel_raw
    s.run(2.0, roll_deg=40, flex=1.0)
    assert s.e.sel_raw == sel


def test_held_fist_auto_turn_stops_at_the_end_of_a_slides_ring():
    s = Sim(n=8)
    s.run(3.1)
    s.e.enter_level(5, s.t, selected=1, limit=(0, 4), fixed_step=26.0)
    s.run(0.3, flex=1.0)
    s.turn(0, 50, seconds=0.3, flex=1.0)
    s.run(3.0, roll_deg=50, flex=1.0)
    assert s.e.sel_raw == 0


@pytest.mark.parametrize("fps", [30, 12, 8, 6])
def test_palm_hold_opens_on_a_slow_camera(fps):
    """Frames far apart (a slow camera or machine) are not gaps: the hold still takes its 3 s."""
    e = GestureEngine(8, GestureSettings())
    t, events = 0.0, []
    for _ in range(int(4.0 * fps)):
        t += 1 / fps
        events += [(t, *ev) for ev in e.update(feats(), t)]
    assert [ev[1:] for ev in events] == [("open", "still")]
    assert 2.95 <= events[0][0] <= 3.0 + 2 / fps
