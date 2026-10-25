import base64
import hashlib
import json
import os
import sqlite3
import time
from pathlib import Path

import pytest

from rotary import actions, apps, integrations, obs, winapi
from rotary.app import FACE_SETTLE, TOUCH_DEPTH, FaceTouch, face_contact, hand_at_face, pick_hand
from rotary.gestures import extract_features
from tests.synth import make_hand


# ------------------------------------------------------------------ Steam
def _write(p: Path, text: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def _acf(appid, name, last, flags=4):
    return f'''"AppState"
{{
\t"appid"\t\t"{appid}"
\t"name"\t\t"{name}"
\t"StateFlags"\t\t"{flags}"
\t"installdir"\t\t"{name}"
\t"LastPlayed"\t\t"{last}"
}}'''


def test_parse_vdf_nested_and_escapes():
    d = apps.parse_vdf('"a"\n{\n\t"b"\t"1"\n\t"c"\n\t{\n\t\t"path"\t"C:\\\\Games \\"x\\""\n\t}\n}')
    assert d == {"a": {"b": "1", "c": {"path": 'C:\\Games "x"'}}}


def test_steam_library_reads_all_libraries_newest_first(tmp_path):
    root, second = tmp_path / "Steam", tmp_path / "Lib2"
    _write(root / "steamapps" / "libraryfolders.vdf",
           f'"libraryfolders"\n{{\n"0"\n{{\n"path" "{str(root).replace(chr(92), chr(92) * 2)}"\n}}\n'
           f'"1"\n{{\n"path" "{str(second).replace(chr(92), chr(92) * 2)}"\n}}\n}}')
    _write(root / "steamapps" / "appmanifest_10.acf", _acf(10, "Old Game™", 100))
    _write(root / "steamapps" / "appmanifest_228980.acf", _acf(228980, "Steamworks Common Redistributables", 999))
    _write(root / "steamapps" / "appmanifest_30.acf", _acf(30, "Half Downloaded", 500, flags=1026))
    _write(second / "steamapps" / "appmanifest_20.acf", _acf(20, "New Game", 300))
    icon = root / "appcache" / "librarycache" / "20" / ("a" * 40 + ".jpg")
    _write(icon, "x")
    games = apps.steam_library(str(root))
    assert [g.name for g in games] == ["New Game", "Old Game™"]
    assert games[0].icon == str(icon)


# ------------------------------------------------------------------ VS Code recents
def test_vscode_recents_prefers_history_then_recent_workspaces(tmp_path):
    storage = tmp_path / "Code" / "User" / "globalStorage"
    storage.mkdir(parents=True)
    a, b, c = (tmp_path / n for n in ("alpha", "beta", "gamma"))
    for d in (a, b, c):
        d.mkdir()
    con = sqlite3.connect(storage / "state.vscdb")
    con.execute("CREATE TABLE ItemTable (key TEXT, value TEXT)")
    con.execute("INSERT INTO ItemTable VALUES (?, ?)", ("history.recentlyOpenedPathsList",
                                                       json.dumps({"entries": [{"folderUri": c.as_uri()},
                                                                               {"fileUri": "file:///x.txt"},
                                                                               {"folderUri": "vscode-remote://wsl/x"}]})))
    con.commit()
    con.close()
    ws = storage.parent / "workspaceStorage"
    for i, folder in enumerate((a, b)):
        d = ws / f"h{i}"
        d.mkdir(parents=True)
        (d / "workspace.json").write_text(json.dumps({"folder": folder.as_uri()}), encoding="utf-8")
        t = time.time() - (100 if folder == a else 10)        # beta used more recently
        os.utime(d / "workspace.json", (t, t))
    got = [os.path.basename(p) for p, _ in integrations.vscode_recents(storage)]
    assert got == ["gamma", "beta", "alpha"]


def test_uri_to_path_windows_drive():
    p = integrations.uri_to_path("file:///c%3A/Users/me/My%20Project")
    assert p.lower().replace("/", "\\").endswith(r"users\me\my project")
    assert integrations.uri_to_path("vscode-remote://ssh/x") == ""


# ------------------------------------------------------------------ catalogue & matching
@pytest.mark.parametrize("name,want,ok", [
    ("OBS Studio", "OBS Studio", True), ("OBS Studio (64bit)", "OBS Studio", True),
    ("Blender 4.2", "Blender", True), ("Visual Studio 2022", "Visual Studio", True),
    ("EA Sports FC 25", "EA", False), ("Steam Support Center", "Steam", False), ("XBOX", "Xbox", True),
    ("SOLIDWORKS 2024", "SOLIDWORKS", True), ("SOLIDWORKS 2024 SP2.0", "SOLIDWORKS", True),
    ("AutoCAD 2025 - English", "AutoCAD", True), ("MATLAB R2024a", "MATLAB", True), ("KiCad 8.0", "KiCad", True),
    ("SOLIDWORKS eDrawings 2024", "SOLIDWORKS", False), ("AutoCAD LT 2025", "AutoCAD", False),
    ("MATLAB Runtime", "MATLAB", False),
])
def test_start_menu_name_matching(name, want, ok):
    assert apps.name_matches(name, want) is ok


def test_catalogue_is_well_formed():
    ids = [s.id for s in integrations.CATALOG]
    assert len(ids) == len(set(ids))
    for s in integrations.CATALOG:
        assert s.category in integrations.CATEGORIES
        assert len(s.glyph) == 4 and int(s.glyph, 16)
        assert s.actions is not None


def test_quicknav_is_capped_and_items_are_valid(monkeypatch):
    spec = integrations.BY_ID["steam"]
    a = integrations.App(spec, target="steam.exe", exe="", installed=True)
    monkeypatch.setattr(integrations, "get", lambda app_id: a)
    many = [integrations.item(f"Game {i}", "Play", "E7FC", "#000000", integrations.open_uri(f"steam://rungameid/{i}"))
            for i in range(40)]
    monkeypatch.setitem(integrations._dynamic, "steam", (time.time(), many))
    qn = integrations.quicknav("steam")
    assert len(qn) == integrations.QUICKNAV_MAX
    assert qn[0]["name"] == "Open Steam"
    assert all(actions.item_from(d) is not None for d in qn)
    q = integrations.quicknav_item("steam")
    assert q["action"] == {"type": "quicknav", "app": "steam"}


def test_every_builtin_action_list_builds():
    for s in integrations.CATALOG:
        a = integrations.App(s, target="x.exe", exe="", installed=True)
        for d in s.actions(a):
            assert actions.item_from(d) is not None, (s.id, d)
            if d["action"]["type"] in ("keys", "window_keys"):
                for k in winapi.parse_keys(d["action"]["keys"]):
                    winapi.vk_code(k)            # every shortcut is pressable


def test_every_catalogue_entry_builds_on_every_system(monkeypatch):
    """CATALOG only holds this system's apps, so build the Windows-only ones (SOLIDWORKS, Medal…) here too."""
    monkeypatch.setattr("rotary.live._medal_folder", lambda: Path("Videos") / "Medal")
    ids = [s.id for s in integrations._ALL]
    assert len(ids) == len(set(ids))
    for s in integrations._ALL:
        assert s.category in integrations.CATEGORIES + ["Windows", "macOS", "Linux"], s.id
        assert int(s.glyph, 16) and s.actions is not None
        a = integrations.App(s, target="x.exe", exe="", installed=True)
        built = s.actions(a)
        assert built and len(built) <= integrations.QUICKNAV_MAX, s.id
        for d in built:
            assert actions.item_from(d) is not None, (s.id, d)
            if d["action"]["type"] in ("keys", "window_keys"):
                for k in winapi.parse_keys(d["action"]["keys"]):
                    winapi.vk_code(k)


def test_engineering_apps_press_their_own_shortcuts(monkeypatch):
    monkeypatch.setattr(integrations, "MAC", False)
    sw = integrations.App(next(s for s in integrations._ALL if s.id == "solidworks"), exe="", installed=True)
    by_name = {d["name"]: d["action"] for d in sw.spec.actions(sw)}
    assert by_name["Rebuild"] == {"type": "window_keys", "process": "SLDWORKS.exe", "keys": "ctrl+b",
                                  "restore": False, "label": "SOLIDWORKS"}
    assert by_name["Zoom to fit"]["keys"] == "f"
    medal = integrations.App(next(s for s in integrations._ALL if s.id == "medal"), installed=True)
    monkeypatch.setattr("rotary.live._medal_folder", lambda: Path("clips"))
    clip = next(d for d in medal.spec.actions(medal) if d["name"] == "Clip that")
    assert clip["action"] == {"type": "keys", "keys": "f8"}         # pressed in the game, not in Medal
    assert integrations.key_label("pageup") == "PgUp"


# ------------------------------------------------------------------ keys, config, python
def test_parse_keys_symbols_and_aliases():
    assert winapi.parse_keys("win+.") == ["win", "."]
    assert winapi.parse_keys("Ctrl+Shift+Esc") == ["ctrl", "shift", "esc"]
    assert winapi.parse_keys("ctrl++") == ["ctrl", "="]
    assert winapi.parse_keys(["Meta", "D"]) == ["win", "d"]
    assert winapi.vk_code(".") == 0xBE and winapi.vk_code("f12") == 0x7B


def test_broken_config_is_backed_up_not_lost(tmp_path):
    p = tmp_path / "config.json"
    p.write_text("{ this is not json", encoding="utf-8")
    cfg = actions.load_config(p)
    assert cfg["items"]
    backups = list(tmp_path.glob("config.broken-*.json"))
    assert len(backups) == 1 and backups[0].read_text(encoding="utf-8") == "{ this is not json"


def test_editor_only_keys_are_not_saved(tmp_path):
    p = tmp_path / "config.json"
    actions.save_config({"items": [{"name": "x", "_auto_name": "x", "action": {"type": "keys", "_tmp": 1}}]}, p)
    saved = json.loads(p.read_text(encoding="utf-8"))
    assert saved == {"items": [{"name": "x", "action": {"type": "keys"}}]}


def test_python_command_bundled_runs_code(tmp_path, monkeypatch):
    monkeypatch.setattr(actions, "SCRIPTS", tmp_path)
    args, err = actions.python_command("print('hi')", {"general": {"python": "bundled"}})
    assert Path(args[-1]).read_text(encoding="utf-8") == "print('hi')"
    actions._run_checked(args, "Python", err_file=err)          # exits 0 → no exception
    args, err = actions.python_command("raise ValueError('boom')", {"general": {"python": "bundled"}})
    with pytest.raises(RuntimeError, match="boom"):
        actions._run_checked(args, "Python", err_file=err)


# ------------------------------------------------------------------ OBS & secrets
def test_obs_auth_matches_protocol_spec():
    pw, salt, challenge = "supersecretpassword", "lM1GncleQOaCu9lT1yeUZhFYnqhsLLP1G5lAGo3ixaI=", \
        "+IxH4CnCiqpX1rM9scsNynZzbOe4KhDeYcTNS3PDaeY="
    secret = base64.b64encode(hashlib.sha256((pw + salt).encode()).digest()).decode()
    expected = base64.b64encode(hashlib.sha256((secret + challenge).encode()).digest()).decode()
    assert obs.auth_string(pw, salt, challenge) == expected


def test_password_encryption_roundtrip(monkeypatch):
    if not winapi.WIN:     # keep the test away from the real Keychain / keyring
        monkeypatch.setattr(winapi.posix, "MAC", False)
        monkeypatch.setattr(winapi.posix.shutil, "which", lambda name: None)
    token = winapi.protect("hunter2 ✓")
    assert token and "hunter2" not in token
    assert winapi.unprotect(token) == "hunter2 ✓"
    cfg = {"integrations": {"obs": {"host": "127.0.0.1", "port": 4456, "password_dpapi": token}}}
    assert obs.settings_from(cfg) == ("127.0.0.1", 4456, "hunter2 ✓")


def test_obs_unreachable_without_exe_explains(monkeypatch):
    monkeypatch.setattr(winapi, "is_running", lambda name: False)
    cfg = {"integrations": {"obs": {"host": "127.0.0.1", "port": 1}}}
    with pytest.raises(obs.OBSError):
        obs.run({"type": "obs", "request": "ToggleRecord"}, cfg, obs_exe="")


# ------------------------------------------------------------------ hand choice & the face filter
FACE = (0.4, 0.15, 0.6, 0.45)        # x0, y0, x1, y1 (normalised)


def _feat(**kw):
    return extract_features(make_hand(**kw))


# FACE is 0.3 image heights tall; a real palm (~0.09 m) at the face's distance is then ~0.17 image heights.
AT_FACE_SIZE = 0.17


def test_chin_resting_hand_is_at_face():
    assert hand_at_face(_feat(flex=1.0, center=(0.5, 0.52), size=AT_FACE_SIZE), [FACE], 4 / 3)     # under the chin
    assert hand_at_face(_feat(center=(0.5, 0.32), size=AT_FACE_SIZE), [FACE], 4 / 3)               # over the face
    assert not hand_at_face(_feat(center=(0.8, 0.30), size=AT_FACE_SIZE), [FACE], 4 / 3)          # beside the head
    assert not hand_at_face(_feat(flex=1.0, center=(0.5, 0.52), size=AT_FACE_SIZE), [], 4 / 3)     # no face seen


def test_hand_held_up_in_front_of_the_face_is_not_at_face():
    for size in (0.26, 0.32):                       # clearly closer to the camera than the face behind it
        f = _feat(center=(0.5, 0.32), size=size)
        at, depth = face_contact(f, [FACE], 4 / 3)
        assert not at and depth > TOUCH_DEPTH
    tilted = _feat(center=(0.5, 0.32), size=0.30, roll_deg=40)
    assert not hand_at_face(tilted, [FACE], 4 / 3)


def test_fingertips_on_the_face_count_as_touching():
    # palm beside the face, fingers reaching onto the cheek (scratching)
    f = _feat(center=(0.68, 0.40), size=AT_FACE_SIZE, roll_deg=-60)
    assert any(FACE[0] <= x <= FACE[2] and FACE[1] <= y <= FACE[3] for x, y in f.tips)
    assert hand_at_face(f, [FACE], 4 / 3)


def test_face_touch_has_hysteresis():
    f = _feat(center=(0.5, 0.32), size=AT_FACE_SIZE * 1.25)     # between TOUCH_DEPTH and TOUCH_DEPTH_EXIT
    assert not hand_at_face(f, [FACE], 4 / 3)
    assert hand_at_face(f, [FACE], 4 / 3, was=True)      # was touching: has to come further forward to let go


def test_face_touch_counts_only_once_it_lasts():
    touch = FaceTouch()
    on_face = _feat(center=(0.5, 0.32), size=AT_FACE_SIZE)
    away = _feat(center=(0.8, 0.30), size=AT_FACE_SIZE)
    assert touch.update(on_face, [FACE], 4 / 3, True, 0.0)[0] is False      # passing by: not yet
    assert touch.update(away, [FACE], 4 / 3, True, 0.1)[0] is False
    assert touch.update(on_face, [FACE], 4 / 3, True, 0.2)[0] is False      # the moment starts over
    assert touch.update(on_face, [FACE], 4 / 3, True, 0.2 + FACE_SETTLE * 0.9)[0] is False
    assert touch.update(on_face, [FACE], 4 / 3, True, 0.2 + FACE_SETTLE)[0] is True   # resting there
    assert touch.update(on_face, [FACE], 4 / 3, False, 1.0)[0] is False     # another hand starts afresh


def test_face_depth_ignores_how_the_hand_is_turned():
    flat = _feat(size=0.2)
    rolled = _feat(size=0.2, roll_deg=70)
    fist = _feat(size=0.2, flex=1.0)
    assert abs(rolled.px_per_m / flat.px_per_m - 1) < 0.05
    assert abs(fist.px_per_m / flat.px_per_m - 1) < 0.05


def test_pick_hand_prefers_the_presented_hand():
    resting = extract_features(make_hand(flex=1.0, center=(0.5, 0.55), size=AT_FACE_SIZE))
    raised = extract_features(make_hand(center=(0.8, 0.35), size=AT_FACE_SIZE))
    i, _ = pick_hand([resting, raised], [FACE], None)
    assert i == 1


def test_old_terminal_fallback_is_removed_from_other_apps(tmp_path):
    p = tmp_path / "config.json"
    p.write_text(json.dumps({"items": [
        {"name": "Steam", "action": {"type": "app", "target": r"C:\Start Menu\Steam.lnk", "fallback": "cmd.exe"}},
        {"name": "Terminal", "action": {"type": "app", "target": "wt.exe", "fallback": "cmd.exe"}}]}), encoding="utf-8")
    items = actions.load_config(p)["items"]
    assert "fallback" not in items[0]["action"]
    assert items[1]["action"]["fallback"] == "cmd.exe"
