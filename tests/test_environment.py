"""Surroundings: a dark room, a window behind you or other people in view are noticed and a fix is suggested
(never applied unasked); Low-light boost brightens only the tracked picture; Focus on me follows only you."""
import numpy as np

from rotary import environment as env
from rotary.actions import DEFAULT_CONFIG
from rotary.camera import Enhancer
from rotary.environment import EnvironmentMonitor, FocusOnMe, SceneSample


def feed(mon, n, **kw):
    for i in range(n):
        mon.add(SceneSample(t=float(i), **{"light": 120.0, "faces": 1, "face_light": 110.0, **kw}))


def cam(**kw):
    return {**env.DEFAULTS, **kw}


def test_nothing_is_suggested_from_a_few_seconds_or_a_normal_room():
    mon = EnvironmentMonitor()
    feed(mon, env.MIN_SAMPLES - 1, light=10.0)
    assert mon.suggest(cam(), 100.0) is None                    # not enough to go by yet
    mon = EnvironmentMonitor()
    feed(mon, env.WINDOW)
    assert mon.suggest(cam(), 100.0) is None


def test_a_dark_room_suggests_low_light_boost_once():
    mon = EnvironmentMonitor()
    feed(mon, env.WINDOW, light=20.0, face_light=15.0)
    s = mon.suggest(cam(), 100.0)
    assert s.kind == "dark" and s.setting == "low_light_boost"
    assert "Dark" in s.reading
    assert mon.suggest(cam(), 101.0) is None                    # still waiting for an answer: asked once


def test_a_bright_window_behind_you_suggests_low_light_boost():
    mon = EnvironmentMonitor()
    feed(mon, env.WINDOW, light=150.0, face_light=50.0)
    s = mon.suggest(cam(), 100.0)
    assert s.kind == "backlit" and s.setting == "low_light_boost"
    assert "lit from behind" in mon.reading()


def test_other_people_in_view_suggest_focus_on_me():
    mon = EnvironmentMonitor()
    feed(mon, env.WINDOW, faces=3)
    s = mon.suggest(cam(), 100.0)
    assert s.kind == "crowded" and s.setting == "focus_on_me"
    assert "3 people in view" in mon.reading()


def test_nothing_is_suggested_that_is_already_on_turned_off_or_never_wanted():
    for c in (cam(low_light_boost=True), cam(suggest=False), cam(never=["dark"])):
        mon = EnvironmentMonitor()
        feed(mon, env.WINDOW, light=20.0)
        assert mon.suggest(c, 100.0) is None


def test_not_now_holds_a_suggestion_back_for_a_while():
    mon = EnvironmentMonitor()
    feed(mon, env.WINDOW, light=20.0)
    assert mon.suggest(cam(), 100.0).kind == "dark"
    mon.answered(False, 100.0)
    feed(mon, env.WINDOW, light=20.0)
    assert mon.suggest(cam(), 200.0) is None
    assert mon.suggest(cam(), 100.0 + env.COOLDOWN + 1).kind == "dark"


def test_the_default_config_has_every_setting_off_except_suggestions():
    got = env.settings(DEFAULT_CONFIG)
    assert got == {"suggest": True, "low_light_boost": False, "focus_on_me": False, "never": []}
    assert env.settings({}) == got and env.settings({"camera": {"never": [3, "dark"]}})["never"] == ["dark"]


# --------------------------------------------------------------------------- low-light boost
def test_boost_brightens_a_dark_picture_more_than_the_usual_correction():
    frame = np.full((480, 640, 3), 25, np.uint8)
    frame[::7, ::5] = 40                                         # some texture for local contrast to work on
    plain, boosted = Enhancer(), Enhancer()
    boosted.boost = True
    a, b = plain(frame.copy()), boosted(frame.copy())
    assert plain.light < 45 and abs(plain.light - boosted.light) < 1    # the raw picture is what's measured
    assert b.mean() > a.mean() > frame.mean()


def test_boost_leaves_a_bright_picture_close_to_how_it_was():
    frame = np.full((480, 640, 3), 170, np.uint8)
    e = Enhancer()
    e.boost = True
    assert abs(float(e(frame).mean()) - 170) < 25


def test_turning_boost_on_takes_effect_on_the_next_frame():
    frame = np.full((480, 640, 3), 25, np.uint8)
    e = Enhancer()
    first = e(frame.copy()).mean()
    e.boost = True
    assert e(frame.copy()).mean() > first


# --------------------------------------------------------------------------- focus on me
class Hand:
    def __init__(self, x, y, px_per_m):
        self.center, self.px_per_m = (x, y), px_per_m


ASPECT = 4 / 3
ME = (0.40, 0.30, 0.60, 0.55)          # big face in the middle: nearest the camera
BEHIND = (0.05, 0.20, 0.13, 0.30)      # small face to the left: someone further back


def face_scale(face):
    return max((face[2] - face[0]) * ASPECT, face[3] - face[1]) / env.FACE_SIZE_M


def test_the_nearest_face_is_the_users():
    assert env.primary_face([BEHIND, ME]) == ME
    assert env.primary_face([]) is None


def test_only_the_users_hands_are_kept():
    f = FocusOnMe()
    mine = Hand(0.5 * ASPECT + 0.25, 0.5, face_scale(ME) * 1.5)              # held up beside my face, nearer
    theirs = Hand(0.09 * ASPECT, 0.33, face_scale(BEHIND) * 1.2)            # by the face further back
    far_off = Hand(0.5 * ASPECT + 1.3, 0.45, face_scale(ME) * 1.2)          # out of my reach
    assert f.keep([mine, theirs, far_off], [ME, BEHIND], ASPECT, 0.0) == [0]


def test_with_no_face_seen_every_hand_is_kept():
    assert FocusOnMe().keep([Hand(0.2, 0.5, 5), Hand(1.1, 0.5, 5)], [], ASPECT, 0.0) == [0, 1]


def test_a_hand_in_front_of_your_face_doesnt_hand_control_to_someone_behind_you():
    f = FocusOnMe()
    assert f.update([ME, BEHIND], 0.0) == ME
    assert f.update([BEHIND], 1.0) == ME                 # my face hidden for a moment: still me
    assert f.update([BEHIND], 1.0 + env.HOLD + 0.1) == BEHIND   # gone for good: the other person now


def test_the_followed_face_stays_while_its_close_in_size():
    f = FocusOnMe()
    other = (0.62, 0.30, 0.81, 0.55)                    # someone beside you, about as near
    assert f.update([ME, other], 0.0) == ME
    bigger_other = (0.62, 0.28, 0.83, 0.56)
    assert f.update([ME, bigger_other], 0.5) == ME       # a touch bigger isn't enough to switch


def test_scene_sample_measures_the_raw_picture_and_the_nearest_face():
    from rotary.tracker import TrackerThread
    raw = np.full((480, 640, 3), 160, np.uint8)
    raw[120:300, 260:380] = 40                          # a face in shadow
    e = Enhancer()
    e(raw.copy())
    s = TrackerThread._scene(None, None, 0, raw, e, {"faces": [(260 / 640, 120 / 480, 380 / 640, 300 / 480)]},
                             True, 5.0)
    assert s.faces == 1 and abs(s.face_light - 40) < 1 and s.light > 140 and s.t == 5.0


def test_answering_a_suggestion_changes_only_what_was_accepted():
    from types import SimpleNamespace

    from rotary.app import RotaryApp
    calls = []
    fake = SimpleNamespace(cfg={"camera": dict(env.DEFAULTS)}, env=EnvironmentMonitor(),
                           apply_config=lambda: calls.append("apply"), _flash_msg=lambda *a, **k: None,
                           window=SimpleNamespace(page=lambda key: None))
    dark = env.Suggestion("dark", "low_light_boost", "", "", "")
    RotaryApp._answer_environment(fake, dark, accepted=False)
    assert fake.cfg["camera"]["low_light_boost"] is False and fake.cfg["camera"]["never"] == []
    RotaryApp._answer_environment(fake, env.Suggestion("crowded", "focus_on_me", "", "", ""), False, never=True)
    assert fake.cfg["camera"]["focus_on_me"] is False and fake.cfg["camera"]["never"] == ["crowded"]
    RotaryApp._answer_environment(fake, dark, accepted=True)
    assert fake.cfg["camera"]["low_light_boost"] is True and calls == ["apply"] * 3
