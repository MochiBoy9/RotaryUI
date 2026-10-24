# Changelog

## [Unreleased]

### Added
- **The notch on any edge.** Bring the notch to the bottom, left or right edge of the screen (drag it there or
  throw it) and it hangs from that edge just like it does from the top: slide it along, pull it away to tear it
  off. It remembers which edge it was on.

### Fixed
- **A free notch couldn't go near the bottom of the screen.** It stopped a few hundred pixels short (most
  noticeable on 1440p and larger screens). It now goes all the way down, and in the lower half of the screen its
  cards open upwards so they stay on screen.

## [2.1.0]

### Fixed
- **Slides, video and volume rings, and the mouse on the rotary.** Opening the rotary failed half-way
  on Windows (a mismatch in how the rotary's screen position was asked for), so the check for PowerPoint, Google
  Slides, YouTube, Netflix, VLC and the rest never ran, the volume ring never appeared, and the mouse (scroll,
  drag, click, right-click) never got hold of the ring, however it was opened. The check also looks
  past RotaryUI's own windows now, so opening from the notch or the tray still finds the app underneath.
- **Pinch.** A touching pinch often read as slightly apart (the camera guesses depth), and the other fingers had to
  be fully open. It's now measured from both the 3D hand and the picture, counts with the other fingers relaxed,
  takes two frames instead of three, and a pinch that tips the palm away for a moment still clicks.
- **"Touching your face" when you weren't.** A hand held up in front of the face has to be closer to the face
  before it counts as touching it, so gestures made in front of the face work more often.

### Changed
- **Far less CPU.** The rotary is drawn at most 60 times a second (not once per refresh on a 120/144 Hz screen),
  and not at all while nothing on it moves: held still, it uses about a tenth of what it did. The camera runs at
  30 fps and hand tracking looks at up to 30 frames a second with a hand about and 12 with nobody around.
  (MediaPipe's Windows build can't run hand tracking on the GPU, so it stays on the CPU, using less of it.)
- **Hand tracking keeps hold of hands more readily** in dim light, side-on and when moving fast; the 30 fps camera
  also gets brighter, less noisy pictures.
- **The window is plain now.** Cards, the sidebar and the background are solid; liquid glass stays on the rotary.
  The *Window background* setting is gone.

## [2.0.0] — RotaryUI

### Added
- **Resize windows with both hands.** Pinch with both hands, then pull them apart to make the window in front
  bigger or push them together to make it smaller. Hands side by side change the width, one above the other the
  height, and hands at opposite corners change both. It only touches the window in front, and only while it's
  windowed (not maximised, minimised or full screen); it never goes past the screen or below a usable size.
  Let go of the pinch to stop, and pinch again to keep going. Turn it off under *Controls → Resize windows*.
  macOS needs the Accessibility permission; Linux needs `xdotool` (X11 / XWayland apps).
- **A first-run tour.** After the welcome page, new users get a short tour that floats beside the rotary: how
  to show your palm, open the rotary with a fist or by holding still, turn it, turn it like a knob, launch with a
  pinch or a pointed finger, put it away, change the volume with two fingers, what it ignores, and how to use it
  all without a camera (the notch, the shortcut, the middle mouse button). Each card shows the hand shape, ticks
  when you do the gesture and moves on. Launching is only practice while it's open. Skip it any time, and replay
  it from *Home*, *General › Help* or the tray menu. Cards follow your settings: no fist card if a fist doesn't
  open the rotary, no gesture cards with the camera off. Existing users aren't shown it automatically.
- **Move the notch.** Drag it along the top edge, or pull it down until it stretches and tears off like a drop of
  liquid, and put it anywhere. Throw it and it glides, bouncing off the screen's edges; bring it back to the top
  and it merges in. It stays where you left it. A middle click (the scroll wheel) sends it home to the middle.
- **Calls ringing in.** When Discord, Teams or another call app rings, the notch opens by itself — even while you
  use the camera — with who's calling (when the app says) and Accept / Decline. Windows.
- **Leave a call** from the notch: hover the call for Leave (Teams, Zoom, Slack; Discord once its *Disconnect*
  keybind is set to Ctrl + Shift + H), or right-click → Leave the call.
- **The mouse works on the rotary wherever it is.** Whenever the rotary is on screen — opened by a hand, the
  mouse, the keyboard or the notch — you can scroll over the ring to turn it, press and drag it around like a
  knob, and click an item or the centre (the app's icon) to launch it. These are caught before the app
  underneath sees them, so turning and clicking the rotary works the same over a web browser, a game or any
  other app. (On Linux the ring still turns, but the app underneath may receive the clicks as well.)
- **A fist keeps turning when you hold it.** Like leaning an open palm, holding your fist turned to one side now
  keeps the ring stepping on by itself — faster the further you turn — until you bring your hand back, as well
  as the firm click-per-item knob it already was. How long to hold is in *Controls → Fist keeps turning*.
- **The volume gets a full-size ring of its own.** Changing the volume with two fingers (or the volume button)
  now shows a full rotary volume ring in a corner of the screen, with the same turning and degree physics as the
  slides and video rings, instead of a small bar. Its corner is set in *Controls → Volume ring corner*.

### Changed
- **Gesture Rotary is now RotaryUI.** The app, its window, menus, shortcuts, installer and downloads carry the
  new name. Your settings, saved keys and start-at-login carry over, and copies you already have update to 2.0 by
  themselves. The settings folder and the program's file names on Windows and Linux keep the old name for that;
  on Linux the app now also runs as `rotaryui`.
- **Screen assist always attempts an answer.** With no question on screen it goes by context clues (what's typed
  into a Google search or the address bar, selected text, an error, the app and window title, which are now sent
  along with the screenshot) and, failing that, sums up the screen: on a Steam store page, what the game is, its
  price and reviews. *No question or problem on screen* is gone.
- **Longer, more detailed answers on hover.** The notch still shows the short answer; hovering it now shows a few
  sentences with the working, the reasoning or the next steps (the card is taller to fit them).
- **Copy copies the detailed answer** instead of the short one.

### Fixed
- **Moving your hand away from your face no longer breaks tracking.** A hand moving out from in front of the
  face (or past the chin) could flicker as "touching the face" for a frame or two, which reset the hold-to-open
  timer and spoiled a fist being formed. A hand now has to rest against the face for a moment before it counts,
  and a brief glitch pauses the open-palm hold instead of starting it over.

## [1.10.0] — Gestures in front of your face

### Changed
- **Gestures work in front of your face.** A hand used to be ignored whenever it overlapped your face on camera,
  so anything done with your head behind it failed. Now a hand is only ignored when it is actually touching your
  face (chin on your hand, a hand on your cheek, scratching): the app compares how big the hand and the face look
  to tell a hand held up in front of the face from one resting on it. Fingertips reaching into the face count too.
- A hand in front of the face stays tracked more readily (the camera view shows *In front of your face*).

## [1.9.1] — Stability and security

### Fixed
- **Settings are no longer reset by a half-saved config.json.** Editing it by hand and saving a typo (or an
  editor saving in two steps) used to set the file aside and start over from the defaults, saved API keys and
  all; now the app keeps running on the settings it has and says the file has a mistake in it.
- A *Command*, *PowerShell* or *Python* item that keeps running and writes a lot of errors no longer hangs, and
  when one fails after printing a lot, the message shows the error it ended with.
- The weather on the rotary works in the packaged Mac app (it couldn't check the server's certificate).
- Opening a Quick nav ring or the Apps page no longer freezes for a few seconds every five minutes while the
  installed apps are looked up again; the last scan answers straight away and a new one runs in the background.
- Windows: updates from this version on also work when the user folder has an accent or a % in its name.
- macOS and Linux: two people signed in on one computer can each run Gesture Rotary.
- Windows: shortcut icons resolve for user names with an apostrophe (C:\Users\O'Brien).
- An OBS port that isn't a number, and odd model lists from AI services, no longer cause errors.

### Security
- Screen assist never sends an API key over plain http to the internet: a custom address has to be https, unless
  it's this computer or the local network (Ollama, LM Studio, a machine on your network by its name).
- Updates download only from GitHub over https, redirects included, and a download that's far too big is refused.
- config.json is readable only by you on macOS and Linux (where there's no keyring it can hold the OBS password
  and AI keys).

### Faster
- The notch's and the rotary's live glass isn't worked out again when nothing behind them has changed.
- Live activities make one list of running programs every two seconds instead of three; HTTPS connections share
  one certificate store instead of loading it each time; each camera frame is handed to MediaPipe once.

## [1.9.0] — Liquid glass

### New
- **A redesigned Gesture Rotary window.** Liquid glass throughout: the sidebar and every card are panes of
  glass over a softly blurred copy of your desktop wallpaper (or a colourful *Aurora*, or *Plain*, in
  *General › Appearance › Window background*).
- **Home page.** Whether the rotary is ready, a big *Open the rotary* button, every way you can open it, the
  switches people reach for most, and a tile for each section.
- **Search settings.** Type in the sidebar's search field (Ctrl+F, or ⌘F on a Mac) to find any setting; pick a
  result and the window jumps to it and highlights it. Ctrl+1 to Ctrl+9 open the sections in order, and the
  sidebar groups them under *Rotary*, *Features* and *System*.
- **Fonts.** The app now ships with Inter, an open font drawn in the same spirit as Apple's San Francisco, and
  uses it for the window, the rotary, the notch and status messages (macOS keeps its own San Francisco). Pick
  any other installed font in *General › Appearance › Font*.
- **A new app icon:** the rotary itself, a glass ring of coloured items turning to the one under the selection
  lens, so it says what the app does at a glance.
- **Volume from the mouse and keyboard.** When the rotary opens from the notch, the keyboard shortcut or the
  mouse button, a liquid-glass volume button sits beside it, ringed by the current level. Click it (or press **+**
  or **−**) and the ring becomes the volume: it turns itself round to where the volume already is, with the level
  as a thin 0–360° line around it (a full turn is full, 4 % a click); scroll, drag or use the arrow keys, and
  click the button or the centre when done. It's what the two-finger slider does by hand,
  without a camera or a tracked hand.
- **The time, the date and the weather on the rotary**, in a liquid-glass capsule under the ring: *9:41 AM · Fri, Oct
  23 · ☁ 18°*. 12-hour by default; *General → Appearance → Clock on the rotary* switches to 24-hour or turns it
  off, and *Weather* turns the icon and temperature off. The weather comes from wttr.in (no account needed).

### Changed
- Hovering the notch no longer says "Rotary". Hover for a moment with no call, music, timer or clip on it and it
  says *Click to open wheel* (with your keyboard shortcut).
- Inside a Quick nav ring opened from the mouse, the app's name at the top is a *‹ Back* button.
- The two-finger slider's level beside the rotary is liquid glass now, like the ring.
- The rotary is a little smaller by default (540 px, was 600; if you'd kept the old default it changes too), and the
  glass lens at the top is smaller, smaller still on the slides, video and volume rings.

## [1.8.0] — Your own AI model

### New
- **Claude in Screen assist.** Choose *Claude (Anthropic)* on the *Screen assist* page and paste an API key from
  the [Claude Platform](https://platform.claude.com/settings/keys). It uses Claude Opus 5.5 unless you pick
  another model.
- **Pick any model your account has.** *List my models* (or just saving a key) asks the service which models
  your key can use and offers the ones that can see images: Claude Opus, Sonnet and Haiku, OpenAI's GPT-6 Astra,
  Sol and Luna, Gemini's paid Pro models on a billed key, OpenRouter's models, or what you've pulled in Ollama.
  You can still type any model name.

### Changed
- *ChatGPT / OpenAI-compatible* now uses GPT-6 Astra by default (it was GPT-4o mini).
- When a paid service says you've hit your limit, the notch says so instead of mentioning a free limit.

## [1.7.0] — Volume, slides and videos

### New
- **Volume slider.** Hold up your index and middle finger (the others curled) and slide your hand up for louder,
  down for quieter, like a fader. It works whether the rotary is closed or open, in any ring, and the rotary holds
  still meanwhile. The level shows beside the rotary, or on the notch / status pill when it's closed. On Windows
  the volume changes silently, without the volume flyout appearing over a game.
- **Slides ring.** Open the rotary over PowerPoint, Google Slides (the site or its app), Keynote, LibreOffice
  Impress or another slideshow and it turns slides instead: no icons, just a cut between every two slides, a
  firm click per slide so a small wobble never changes slide, and a hard stop at the first and last slide.
  PowerPoint and Keynote say how many slides there are; elsewhere the ring simply steps forward and back.
  Slides change with Page Down / Page Up, like a presentation clicker.
- **Video ring.** Over YouTube, Netflix, Hulu, VLC or another video player, a full turn of the rotary is the
  whole video: turn it to find the moment you want. It turns by itself as the video plays and holds still
  while you turn it, and it stops at the start and the end. The time shows in the middle. Where the system's
  media controls can't move the video (or say how long it is), each click jumps the player's own seek step.
- Click the centre (or press Enter, pinch, or point) on a slides or video ring for your usual items.
- **A mouse button opens the rotary**, from any app, even a full-screen game or video: hold the middle button
  (the wheel), or pick a side button, under *Accessibility → Keyboard & mouse*. A quick middle click still
  works as usual.
- *Controls → Volume, slides and videos* switches the volume slider, the slides ring and the video ring on or off.

### Changed
- While the rotary is open from the mouse, the keyboard or the notch, the wheel turns it wherever the pointer is,
  and clicks and scrolls outside it stop at Gesture Rotary instead of reaching the app underneath (on Linux they
  still reach it). Clicking the rotary or the notch never takes the focus, so full-screen apps and videos stay
  full screen.

## [1.6.0] — Automatic updates and grip to turn

### New
- **Gesture Rotary updates itself.** It checks GitHub for a newer release a minute after it starts and every six
  hours after that. A new version downloads in the background, is checked against the release's checksums, and
  installs when the rotary isn't in use: the app restarts into it with your settings, and the notch says
  *Updated to Gesture Rotary …*.
  - Windows: the Setup.exe install upgrades silently in place; the portable folder is replaced.
  - macOS: the app in Applications is replaced. Linux: `install.sh`'s copy is replaced.
  - If anything goes wrong, the old version stays and starts again.
  - Versions before 1.6.0 can't update themselves: download 1.6.0 once and later versions arrive by themselves.
- *General → Updates*: turn *Update automatically* off, or *Check now* to update right away.
- A 360° gauge around the main rotary shows how far it has turned since it opened (not on Quick nav rings).

### Changed
- **Turn the rotary like a real knob.** Close your hand into a fist and tilt it: the ring follows with a firm
  click for every item and stays put between clicks, so it takes a deliberate turn to move on.
- **Re-grip like a ratchet.** Open your hand and close it again within 1.5 s and the ring doesn't move, so you can
  keep turning past what your wrist reaches in one go. Keep the hand open for 1.5 s to go back to palm mode.
- **Point to launch.** From the fist, stick out your index finger to open the selected item. Letting go no longer
  launches.
- The ring snaps to each item faster.

## [1.5.1] — MIT License

### New
- **Gesture Rotary is now open source under the MIT License.**
- *General → About* shows the license and the third-party software the app includes (Qt, MediaPipe, OpenCV,
  NumPy and more), with the full text of each license.
- Every download includes `LICENSE.txt`, next to the app on Windows and Linux and in the .dmg on macOS. The
  app also carries `THIRD_PARTY_NOTICES.txt` and the full license texts.

## [1.5.0] — Screen assist

### New
- **Screen assist**: press **Win + Alt + A** (Mac: **⌃⌥A**, Linux: **Super + Alt + A**), choose *Screen assist* on the rotary, or
  right-click the notch → *Solve what's on screen*. An AI looks at your screen, finds the question or problem
  on it and answers on the notch:
  - multiple choice: the option to pick (**A**, **B**, **C**…) in a badge, then its text
  - anything else (maths, puzzles, error messages, code, forms): a short answer
  - hover the answer for a sentence on why and a *Copy* button; click it to put it away
- **Free AI services**: Google Gemini (free API key from Google AI Studio, the default), Groq and OpenRouter
  (free tiers), or Ollama, which is free, private and runs on your own computer. ChatGPT and any
  OpenAI-compatible server (LM Studio…) work too. Set it up on the new *Screen assist* page: service, API key,
  model, keyboard shortcut and how long the answer stays.
- API keys are stored like the OBS password: encrypted for your Windows account, in the macOS Keychain, or in
  the Linux keyring. The screenshot goes only to the service you chose, and only when you ask.
- *Rotary → + → Screen assist* adds it to the rotary (or *Add to the rotary* on its page).
- When screen assist is in use, the notch appears even in *Auto* mode with a camera, and over borderless
  full-screen games. With the notch set to *Never*, the answer shows in the status pill instead.

## [1.4.0] — macOS and Linux

### New
- **Gesture Rotary now runs on Macs** (Apple silicon, macOS 14 Sonoma or later) **and on Linux** (x86-64), as
  well as Windows. Hand gestures, the notch, the mouse-and-keyboard rotary, Quick nav and the settings window
  all work the same way.
- **Installers for every system**:
  - Windows: `GestureRotary-<version>-Windows-Setup.exe`, with Start menu and optional Desktop shortcuts,
    an optional start-with-Windows checkbox, and an entry in *Apps → Installed apps* to uninstall it. No
    administrator rights needed. A portable `.zip` is there too.
  - macOS: `GestureRotary-<version>-macOS.dmg`. Drag the app to Applications.
  - Linux: `GestureRotary-<version>-Linux-x86_64.tar.gz`. Unpack it and run `./install.sh` (no root needed).
- On a Mac:
  - The keyboard shortcut is **⌃⌥Space** (Control + Option + Space) and needs no permission.
  - App shortcuts use ⌘ where Windows uses Ctrl (Discord's ⇧⌘M mute, Zoom's ⇧⌘A…).
  - A **macOS** Quick nav covers Lock, Mission Control, Spotlight, Screenshot, Emoji, Launchpad, Activity
    Monitor, Sleep and more.
  - *Any installed app* lists everything in your Applications folders.
  - Settings live in `~/Library/Application Support/GestureRotary`.
  - The OBS password is kept in your Keychain.
  - *Open at login* uses a LaunchAgent.
- On Linux:
  - The shortcut works through X11.
  - *Any installed app* lists your apps menu.
  - **Now playing** on the notch works through `playerctl` (any MPRIS player).
  - Spoken item names use speech-dispatcher or eSpeak.
  - The battery comes from `/sys/class/power_supply`.
  - Settings live in `~/.config/GestureRotary`.
- Battery alerts on the notch also work on Mac laptops.

### Changed
- The icons are drawn from a small bundled font (from Microsoft's MIT-licensed Fluent UI System Icons) where
  Windows' Segoe Fluent Icons isn't installed, so they look right on every system.
- Builds and releases: `tools/build.py` builds and packages for the system it runs on. Pushing a `v*` tag
  builds all three on GitHub Actions and publishes the release.

### Fixed
- `build_exe.bat` no longer fails on a fresh setup because Pillow (used to make the icons) was missing.

### Not available on macOS / Linux (yet)
- Windows-only extras: Xbox Game Bar, Windows' media controls on macOS, the microphone/camera-in-use dots,
  Medal clips, and hiding the rotary from screen capture.
