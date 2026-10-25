# RotaryUI

**Windows · macOS · Linux**

Raise your hand to any webcam and a liquid-glass rotary appears. Lean, slide or turn your hand to
choose, then launch apps, shortcuts, scripts, or the actions inside your apps (Steam games, OBS
recording, Discord mute, recent VS Code projects…). No camera? Click the notch at the top of the screen and
use the same rotary with your mouse and keyboard.

## Install

Download the file for your system from the [latest release](https://github.com/MochiBoy9/Gesture_Rotary/releases/latest).
Everything is bundled (Python, MediaPipe, the hand and face models), so there's nothing else to install.

| System | Download | Install |
| --- | --- | --- |
| **Windows 10 / 11** (64-bit) | `RotaryUI-…-Windows-Setup.exe` | Run it. No administrator rights needed. It adds Start menu (and optionally Desktop) shortcuts and can start with Windows. If SmartScreen appears: *More info → Run anyway*. A portable `.zip` is also available. |
| **macOS 14 Sonoma or later**, Apple silicon (M1 and later) | `RotaryUI-…-macOS.dmg` | Open it and drag **RotaryUI** to **Applications**. The first time, right-click the app → **Open** (on macOS 15+: try to open it, then *System Settings → Privacy & Security → Open Anyway*). |
| **Linux** x86-64 (Ubuntu 22.04+, Debian 12+, Fedora 35+…) | `RotaryUI-…-Linux-x86_64.tar.gz` | `tar xzf RotaryUI-*.tar.gz && cd RotaryUI-*/ && ./install.sh`. This needs no root; the app appears in your apps menu and as `rotaryui`. |

Settings live in `%APPDATA%\GestureRotary` (Windows), `~/Library/Application Support/GestureRotary` (macOS) or
`~/.config/GestureRotary` (Linux).

RotaryUI was called Gesture Rotary before 2.0. The settings folder, the Windows program file (`Gesture Rotary.exe`)
and the Linux command keep the old names so existing copies carry their settings over and keep updating; each
release also carries its files under the old `GestureRotary-…` names for copies older than 2.0.

The window stays on the taskbar or Dock. Closing it minimises it, and there's also a tray (menu bar) icon.
Launching the app again brings the window forward. *General → Uninstall* removes it cleanly, as does
*Apps → Installed apps* on Windows and `./install.sh --uninstall` on Linux.

**Updates install themselves.** A few hours apart (and a minute after it starts), the app asks GitHub for the
latest release. When there's a newer one it downloads the file for your system in the background, checks it
against the release's `SHA256SUMS.txt`, and once the rotary isn't in use it restarts into the new version,
keeping your settings. Turn it off, or check right away, under *General → Updates*. Only an installed copy
updates itself (the Setup.exe install, the portable folder, the app in Applications, or `install.sh`'s copy);
running from source, it just says a new version is out.

### On a Mac

- **Camera**: macOS asks the first time. Video never leaves your Mac.
- **Accessibility** (*System Settings → Privacy & Security → Accessibility*): needed for items that press
  keyboard shortcuts (Mission Control, media keys, Discord's mute…). macOS asks the first time one runs.
- **Screen Recording** is optional. It's only for *live glass*, which blurs what's behind the rotary.
- The keyboard shortcut is **⌃⌥Space** (Control + Option + Space) and needs no permission.
- App shortcuts use ⌘ where Windows uses Ctrl. A **macOS** Quick nav has Lock, Mission Control, Spotlight,
  Screenshot, Emoji, Launchpad, Activity Monitor and Sleep.
- Intel Macs aren't supported: MediaPipe, the hand tracker, has no Intel-Mac build.

### On Linux

- Hand tracking needs OpenGL ES: `sudo apt install libgles2 libegl1` (Fedora: `libglvnd-gles libglvnd-egl`).
  `install.sh` tells you if anything is missing.
- The keyboard shortcut and pressing keys work through X11. Under Wayland they only reach XWayland apps, so use
  the notch or the tray icon to open the rotary.
- Optional: `playerctl` for *now playing* on the notch, `speech-dispatcher` or `espeak-ng` for spoken item
  names, `wmctrl` for items that press a shortcut inside another app, and `xdotool` for resizing windows with both hands.

## The tour

The first time you set the app up, a short tour floats beside the rotary: which hand shapes to use (an open palm,
a fist, a pinch, a pointed finger, two fingers for the volume), how to put the rotary away, and how to do it all
without a camera from the notch, the mouse and the keyboard. Each card has a drawing of the hand shape, and when you
do the gesture it ticks and moves on by itself. Launching an item while the tour is up is only practice, so nothing
opens. *Skip tour* (or Esc) closes it. Replay it any time with *Take the tour* on *Home* or *General › Help*, or
*Show the tour* in the tray menu. With the camera off, the tour skips the gestures.

## Using it (palm toward the camera)

| Do this | Result |
| --- | --- |
| Open palm, then **close it into a fist** | Opens instantly |
| **Hold your palm still** (3 s by default, as low as 0.5 s) | Opens |
| **Lean** your hand (Tilt), **slide** it (Move), or **turn** it (Follow), then hold | Keeps scrolling until you straighten or return to the middle |
| **Point** with your index finger, move it aside, hold | Scrolls; **push** toward the camera to click |
| **Fist**: **tilt** it like turning a knob | Turns one firm click per item and stays there. Open your hand briefly and close it again to re-grip without moving the ring. Or, like an open palm, **hold your fist turned** to one side and it keeps turning by itself until you turn back |
| Fist: **point** your index finger | Launches the selected item |
| Open hand for 1.5 s | Back to palm mode: **pinch** or **hold still** on an item launches |
| Back of the hand, or lower it | Closes |
| **Index + middle finger up**, slide up or down | Changes the volume, whether the rotary is open or not: a full-size volume ring appears in a corner of the screen (*Controls → Volume ring corner*) and turns with the level |
| **Pinch with both hands**, then pull apart or push together | Resizes the window in front (if it's windowed): side by side changes the width, one above the other the height, opposite corners both. Let go of the pinch to stop |
| **Ctrl + Alt + Space** (Mac: **⌃⌥Space**) | Opens from the keyboard: ← → move, Enter launches, Esc closes. Any shortcut, and the keys that move, launch and go back, can be changed in *Accessibility → Keyboard & mouse* |
| **Hold the middle mouse button** | Opens for the mouse, even over a full-screen game (or a side button: *Accessibility*) |

Whenever the rotary is on screen — however you opened it — the mouse works on it directly: **scroll over the ring** to turn it, **drag it around** like a knob, and **click an item or the centre** to launch it. These never reach the app underneath, so it works the same over a browser, a game or the desktop. (On Linux the mouse can turn the ring but the app underneath may see the clicks too.)

### Slides and videos

What's in front decides how the rotary opens:

- **Over a slideshow** (PowerPoint, Google Slides, Keynote, LibreOffice Impress…) it's a **slides ring**: no
  icons, a cut between every two slides, and a firm click per slide so it doesn't change slide by accident. It
  stops at the first and last slide. PowerPoint and Keynote say how many slides there are; with the others it
  simply steps forward and back. Slides change with Page Down / Page Up, like a presentation clicker.
- **Over a video** (YouTube, Netflix, Hulu, VLC, Media Player, mpv, QuickTime…) it's a **video ring**: one full
  turn is the whole video, so turn it to the moment you want. It turns by itself as the video plays, holds still
  while you turn it, and stops at the start and the end. It jumps through the system's media controls (Windows,
  and MPRIS on Linux); where those can't move the video, or on a Mac, each click presses the player's own seek
  key instead.
- Click the centre, press Enter, pinch or point for your usual items. *Controls → Volume, slides and videos*
  turns either ring off.

## Without a camera: the notch and the mouse

A small liquid-glass notch hangs from the top of the screen. By default it appears by itself when there's no
camera to use; the *Notch* page can show it always, never, or tuck it into a thin line until the mouse reaches
it. It hides during full-screen games and videos, shows status messages, and never appears in screenshots or
recordings. Choosing *Mouse & keyboard* there turns the camera off completely: hand tracking never loads, so
the app starts faster and uses less memory.

It's also a Dynamic Island for your desktop. Everything below can be switched on or off on the *Notch* page.
Rows marked *(Windows)* aren't available on macOS / Linux yet.

| Shows | How |
| --- | --- |
| The time | In the middle, with or without the day; 12- or 24-hour (follows the system unless you choose) |
| Now playing | YouTube and other sites in Chrome / Edge / Firefox, Spotify, Apple Music — anything Windows' media controls know (Linux: any MPRIS player, via `playerctl`; not on macOS). Album art and sound bars; hover for title, progress and ⏮ ⏯ ⏭ |
| Clips | A red REC dot while Medal records a game *(Windows)*; "Clip saved" / "Screenshot saved" from Medal, Xbox Game Bar, the NVIDIA app or OBS |
| Timers | Right-click → Timer. Counts down on the notch; hover for +1 min / Cancel; chimes when done |
| Mic & camera | An orange dot while an app uses the microphone, green for the camera (RotaryUI itself doesn't count) *(Windows)* |
| Battery | Plugged in, unplugged, and low-battery alerts on laptops |
| Calls | Time on a Discord, Teams, Zoom, Slack or other call; hover for Mute (and Deafen) and **Leave** *(Windows)* |
| A call ringing in | The notch opens by itself, even while you use the camera: who's calling (when the app says) and the app, with **Accept** and **Decline**. Right-click has them too *(Windows)* |

Saved clips and finished timers can pop the notch up briefly over borderless full-screen games (never over
exclusive full-screen ones). *Look* switches between Dark (like a real notch) and Glass (more see-through).

| Do this | Result |
| --- | --- |
| Click the notch, or scroll on it, press **Ctrl + Alt + Space**, or hold the middle mouse button | Opens the rotary for the mouse and keyboard |
| Scroll (anywhere), or drag around the ring | Turns it |
| Click an item | Brings it to the top; click it again (or the centre) to open it |
| Right-click · Esc · the ‹ at the top | Goes back out of a Quick nav ring, or closes |
| ← → · Enter | Turn · open |
| Click the volume button beside it · **+** / **−** | Turns the ring into the volume: a full turn from silent to full, 4 % a click (scroll up or → for louder). Click the button or the centre when done |
| Click outside it | Closes it |

Hover the notch for a moment with nothing playing on it and it says *Click to open wheel*.

**Moving the notch.** It can hang from any edge of the screen: top, bottom, left or right. Drag it along its edge
to slide it. Pull it away from the edge and it stretches like a drop of liquid until it tears off; then put it
anywhere, right down to the bottom of the screen (in the lower half its cards open upwards). Bring it near an
edge, or throw it at one (let go while it's moving and it glides), and it merges into that edge. It stays where
you left it, next time too. A **middle click** on it (press the scroll wheel) sends it back to the
middle of the top edge; so does *Put the notch back in the middle* in its right-click menu.

**Calls ringing in** are noticed two ways: a call app's own "incoming call" window (its title says who's calling),
or a call app — not already in a call — playing its ringtone for a few seconds. *Accept* and *Decline* press the
app's own shortcuts: Discord Ctrl + Enter / Esc, Teams Ctrl + Shift + S / Ctrl + Shift + D. Apps without one
(WhatsApp, Telegram, Signal…) are brought to the front to answer there; Decline just puts the card away.
**Leave** presses Teams' and Slack's Ctrl + Shift + H and Zoom's Alt + Q. In Discord it presses the *Disconnect*
button itself (through Windows' accessibility interface, so nothing needs setting up and Discord stays where it
is). If Discord is in another language, list its button's name under `"discord_leave_buttons"` in `"island"`
in config.json; failing that RotaryUI presses Ctrl + Shift + H, so you can also give Discord's *Disconnect from
voice channel* keybind that key (*User Settings → Keybinds*), or set yours as `"discord_leave_keys"`.

Under the ring the rotary shows the time (9:41 AM, or 24-hour under *General → Appearance*), the date and the
weather: an icon for sun, cloud, fog, rain, snow or a storm, and the temperature (°F in the US, °C elsewhere).
The weather comes from [wttr.in](https://wttr.in), which works out roughly where you are from your internet
address; switch *Weather* off to stop it asking, or set `"weather_place"` under `"appearance"` in config.json to
a city.

While it's open this way, clicks and scrolls outside the rotary stop at RotaryUI: the app underneath, say a
full-screen game or video, never gets them, and clicking the rotary or the notch never takes the focus, so nothing
leaves full screen. The mouse button works over full-screen apps, where the notch hides. A quick middle click (or
pressing it and dragging) still reaches the app as usual; *Accessibility → Keyboard & mouse* can use the back or
forward side button instead, or none. On a Mac this needs the Accessibility permission; on Linux the clicks and
scrolls also reach the app underneath.

Mouse users can also pick a button that **opens the highlighted item** and one that **closes the rotary** from
anywhere on the screen (*Accessibility → Keyboard & mouse*): left click to open and right click to close, say, so
you scroll to an item and click without aiming at it. Over the ring, a left click still opens the item under the
pointer. The shortcut that opens the rotary (and Screen assist's) can be any combination you press: click it in
Settings and press the keys. *Keys on the open rotary* changes ← → Enter Esc to keys of your own.

A rotary opened by a gesture lets every click through to the windows underneath, as before.

**Quick nav**: an app item marked with a chevron opens a second ring of that app's own actions, with a Back
item. Only installed apps are offered.

| App | What's in its Quick nav |
| --- | --- |
| Steam | Your games, most recently played first, plus Library, Big Picture, Friends, Store, Downloads |
| OBS Studio | Record, pause, stream, save replay, virtual camera, mute mic, and every scene |
| Discord | Open, DMs, mute, deafen, quick switcher |
| VS Code / Cursor | Open, new window, and your recent projects |
| Claude | The Claude app, a new chat, and Claude Code in a terminal |
| Spotify / Apple Music / VLC | Play/pause, next, previous (Spotify: search) |
| Browsers | New window, private window, reopen the last tab |
| Zoom / Teams | Mute, camera |
| Roblox Studio | Play-test, stop |
| Windows | Lock, desktop, task view, snip, clipboard, emoji, quick settings, Task Manager… |
| macOS | Lock, Mission Control, Spotlight, screenshot, emoji, Launchpad, Activity Monitor, Sleep… |
| Linux | Lock, overview, screenshot, files, terminal, settings |
| Xbox Game Bar | Record that, record, screenshot, mic |

There are about 45 apps in total, including Epic, Battle.net, EA, Slack, Notion, Obsidian, Blender, Photoshop,
Figma, Unity Hub and Office. Any other installed app, including Store apps, can be added from
*Rotary → + → Any installed app*.

OBS works with no setup: if OBS is closed, choosing Record starts OBS already recording. To control OBS while it
is open, turn on *Tools → WebSocket Server Settings* in OBS and enter the password in *Apps → OBS Studio*. The
password is encrypted for your Windows account (on a Mac it goes in your Keychain).

## Screen assist (AI)

Press **Win + Alt + A** (Mac: **⌃⌥A**, Linux: **Super + Alt + A**). You can also choose *Screen assist* on the rotary, or right-click the
notch → *Solve what's on screen*. An AI looks at your screen, finds the question or problem on it and answers
on the notch. It always attempts an answer:

- **Multiple choice**: the option to pick in a badge, then its text, e.g. **B** · Mitochondria.
- **Anything else** (maths, a puzzle, an error message, a bug in code, a form): a short answer, e.g. `x = 4`.
- **No question on screen**: it goes by context clues (what you typed into Google or the address bar, selected
  text, the window title) or sums up what's there, e.g. a Steam store page → what the game is, its price and reviews.
- **Hover** the answer for a longer, detailed one (the working, why, next steps), plus *Copy*, which copies that
  detailed answer. **Click** it to put it away. Otherwise it stays for 20 seconds (or 10 / 45 / 90).

It works with free AI services, or with your own paid account. Pick one on the *Screen assist* page:

| Service | Cost | Setup |
| --- | --- | --- |
| **Google Gemini** (default) | Free tier | Get a key at [aistudio.google.com/apikey](https://aistudio.google.com/apikey) with a Google account and paste it in. No card needed. |
| **Groq** | Free tier | Key from [console.groq.com/keys](https://console.groq.com/keys). Very fast. |
| **OpenRouter** | Free models (`…:free`) | Key from [openrouter.ai/keys](https://openrouter.ai/keys). |
| **Ollama** | Free, private | Runs on your own computer, so nothing leaves it. Install [Ollama](https://ollama.com/download), then `ollama pull gemma3` (or another vision model such as `qwen2.5vl`). No key needed. |
| **Claude (Anthropic)** | Paid per use | An API key from [platform.claude.com](https://platform.claude.com/settings/keys). Uses Claude Opus 5.5 unless you pick another model. |
| **ChatGPT / OpenAI-compatible** | Paid per use | An API key from [platform.openai.com](https://platform.openai.com/api-keys). Uses GPT-6 Astra unless you pick another model. Or change the address to use LM Studio or any other OpenAI-compatible server. |

A Claude Pro / Max or ChatGPT Plus / Pro subscription doesn't include API use, so the paid services need an API key
with credit on it. Gemini's Pro models need billing turned on for your Google key.

Every service lets you change the model. Once your key is saved, *List my models* shows the models that key can
use (only the ones that can see images), so you can pick, say, Claude Sonnet 5.5 or GPT-6 Luna. You can also
type any model name. Keys are kept the same way as the OBS password: encrypted for your Windows
account, in the macOS Keychain, or in the Linux keyring. The screenshot (the screen under the mouse) goes only
to the service you chose, and only when you ask. On a Mac, allow **Screen Recording** the first time; without it
the AI only sees your wallpaper. On Linux it needs X11: Wayland doesn't let apps capture the screen this way.

## Accessibility

*Accessibility* offers quick setups:

- **Standard**
- **Palm only**: no fist or pinch; opens in 1 s and launches by holding still.
- **No wrist turning**: slide your hand sideways instead of tilting it.
- **Gentle**: slower and forgiving of tremor.

It also has:

- either side of the hand counts
- camera distance (near / normal / far)
- how steady you need to hold
- inactivity timeout (or never)
- text size, high contrast, reduce motion
- spoken item names
- sound volume
- a keyboard shortcut

Every control in the window works with Tab, arrow keys and screen readers.

## The RotaryUI window

The window opens on **Home**: whether the rotary is ready, a button to open it, every way you can open it and a
few quick switches. The sidebar groups the rest under *Rotary*, *Features* and *System*; its search field
(Ctrl+F, ⌘F on a Mac) finds any setting and takes you straight to it, and Ctrl+1 to Ctrl+9 open the sections.

*General › Appearance* sets the look: the **font** (Inter by default, San Francisco on a Mac, or any font you
have installed; it's used on the rotary and the notch too). The window itself is plain and solid; liquid glass is
kept for the rotary.

## Dark rooms and busy rooms

RotaryUI keeps an eye on the room it sees. When it's been dark for a while, a window or lamp behind you leaves
your face in shadow, or other people are in view, it asks whether to turn on a fix. Nothing changes until you
say yes, and you can switch either fix on or off yourself in *Camera › Surroundings*:

- **Low-light boost** brightens and evens out the picture the hand tracker sees, and smooths the grain of a
  very dark room.
- **Focus on me** follows only the hands of the person closest to the camera, so people sitting behind or
  walking past you can't open or turn the rotary.

Both work inside RotaryUI on the picture it tracks: your camera's settings and your computer's are never
changed. *Don't ask again* stops that suggestion; turning *Suggest fixes* off and on brings them all back.

## Accidental-open protection

The rotary ignores a hand that is:

- stretching or moving fast
- touching your face: propping up your chin, resting on a cheek, scratching (face detection). A hand held up
  *in front of* your face still works: the app compares the hand's size with your face's to tell how far in
  front of it the hand is
- tilted away or partly out of view
- a fist that never showed an open palm first
- not yet identified as left or right

After closing, it waits a moment before it can reopen. *Controls → Accidental-open protection* sets how strict
this is.

## Develop

Windows:

```
dev\setup.bat                           # one-time: Python + dependencies via uv
dev\run-debug.bat                       # run from source with logs and the window open
set UV_PROJECT_ENVIRONMENT=%LOCALAPPDATA%\GestureRotary\venv
build_exe.bat                           # build and install the .exe for yourself (%USERPROFILE%\Apps\Gesture Rotary)
```

macOS / Linux:

```
dev/setup.sh                            # one-time: Python + dependencies via uv
dev/run-debug.sh                        # run from source with logs and the window open
```

Everywhere:

```
uv run pytest -q                        # gestures, glass, notch, live activities, Quick nav, OBS, screen assist…
uv run python tools/render_dial.py out  # render rotary states to PNGs
uv run python tools/render_notch.py out # render the notch's states to one PNG
uv run python tools/render_tutorial.py out  # render every card of the first-run tour (--mouse: camera off)
uv run python tools/shot_window.py out  # screenshots of the window's pages
uv run python tools/selftest.py out     # boot against the real camera, report fps
uv run python tools/build.py            # build the installer for this system into release/
```

`rotary/winapi.py` holds the Windows code and `rotary/posix.py` its macOS / Linux counterparts (Carbon
hotkeys, an X11 key grab, pynput for key presses, LaunchAgents and autostart files). Callers go through
`winapi`, which hands over to `posix` off Windows. Off Windows, icons come from `assets/rotary-icons.ttf`:
Fluent UI System Icons (MIT) placed on the Segoe Fluent Icons code points the app uses. Rebuild it with
`tools/make_icon_font.py` after using a new code point; a test checks that every one is covered.

### Releasing

1. Set the version in `rotary/__init__.py` and `pyproject.toml`, then `uv lock`, and add a section to `CHANGELOG.md`.
2. Get it onto `main` (merge the pull request). That's all: the *Release* workflow sees the new version and
   publishes `v<version>`.

The workflow tests and builds on Windows, macOS and Linux, then publishes a GitHub Release with the Setup.exe,
the portable .zip, the .dmg, the Linux .tar.gz and `SHA256SUMS.txt`. Its notes are the changelog section plus
`installer/RELEASE_NOTES.md`. Pushes to `main` that leave the version alone don't release anything, and an
existing release is never overwritten automatically.

To release by hand, push a tag (`git tag v1.5.1 && git push origin v1.5.1`), or open *Actions → Release → Run
workflow*, pick the branch that has that version, and type the tag (or leave it empty for the branch's own
version). The tag has to match `__version__` on that branch, or the run stops before building and says why.
Typing the tag of an existing release rebuilds it and replaces its files. Pull requests that touch the packaging
only build; download the files from the run's artifacts.

Installed copies pick the new release up by themselves (`rotary/update.py`): they only look at the latest
published release, never a pre-release or a draft, so a `-beta` tag reaches nobody automatically.

The Mac app is signed ad hoc, so users right-click → Open the first time. macOS also ties the Camera and
Accessibility permissions to the signature, so after an automatic update an ad hoc–signed app may ask for them
again; a Developer ID signature keeps them. To sign and notarise it instead, add these
repository secrets:
- `MACOS_SIGN_IDENTITY` (e.g. `Developer ID Application: Name (TEAMID)`)
- `MACOS_CERTIFICATE_P12` (base64) and `MACOS_CERTIFICATE_PASSWORD`
- `APPLE_ID`, `APPLE_TEAM_ID` and `APPLE_APP_PASSWORD`

The glass (`rotary/ui/liquid.py`) is computed with numpy from a grab of the screen behind each window: a light
frost, refraction at the bevelled edges, and highlights from a light at the upper left. The geometry is
cached, so reopening only costs the grab. OpenCV is the slim headless build, and MediaPipe's matplotlib and
sounddevice dependencies are overridden away in `pyproject.toml` (the tracker gives MediaPipe stand-ins).

On Windows, the virtual environment and build folders live under `%LOCALAPPDATA%\GestureRotary` because
MediaPipe's deep file paths break Windows' 260-character path limit in long folders.

## License

RotaryUI is released under the [MIT License](LICENSE). The release builds also bundle Qt / PySide6,
MediaPipe and its models, OpenCV, NumPy and other libraries, each under its own license (LGPL, Apache 2.0,
BSD, MIT and others). [THIRD_PARTY_NOTICES.txt](THIRD_PARTY_NOTICES.txt) lists them, with the full texts in
[licenses/](licenses). The app shows them all under *General → About*, and every download includes them.
