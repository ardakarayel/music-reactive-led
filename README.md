# Music Reactive LED

Make your WiFi LED strip react to music in real time: beat, vocal and drop detection, with a lab for building your own modes.

A Windows desktop app that listens to whatever your PC is playing (Spotify, YouTube, games, anything), analyses it live and drives an RGB LED strip over your local network. No cloud, no microphone, no extra hardware.

> The UI is in Turkish for now. An English translation is a welcome contribution.

## Features

- **Manual control:** color wheel, brightness, quick colors, power, timer.
- **Scenes:** built-in moods plus your own **scene editor**: a color flow of up to 12 steps (color, brightness, smooth or instant transition, hold time), looped or played once, with live preview on the strip or on screen only.
- **Effects:** breathe, rainbow, candle, aurora, police, strobe.
- **Music modes**
  - **Basic:** *Pulse* (brightness follows the beat) and *Spectrum* (color follows the bass/treble balance).
  - **Party / Concert / Chill:** beat-tracked modes with section detection.
  - **Dionysos:** the main mode. It flows like Spectrum, but reacts to the *structure* of the song:
    - the light cuts to black when the music suddenly drops out and bursts back on the return (drop);
    - brightness follows the singer's loudness (vocal tracking);
    - the color changes only at musical events: long silences, drops, emotional turning points in the vocal, or (with a chance you set) after the light dips below a threshold;
    - never white, and at most 3 hard transitions per second (flash safety).
- **Lab:** build and name your own music modes.
  - About 80 parameters as sliders in 6 tabs (Brightness, Color, Events, Vocal, Beat, Sending), each with Basic and Advanced views.
  - **Live** mode (changes reach the strip instantly while the song plays) or **Demo** mode (colors only on screen).
  - Optional **color palette**: pick 2-8 colors and the mode uses only those, shuffled or in order.
  - Live 32-band spectrum, spectrogram and a level meter that draws the trigger thresholds, so you can see why a cut or a drop fired.
  - Now-playing box with play/pause, next and previous.
  - Save, save as, rename, delete and export modes (`modlar.json`).
- Follows the default audio output: switch from speakers to headphones and it keeps working.
- System tray icon, global shortcut `Ctrl+Alt+L` (power on/off), single-instance window.

## How it works

```
WASAPI loopback (what the PC plays)
  -> analysis (analiz/)
       dinleme.py   HPSS split (harmonic / percussive), 6 bands, 32-band spectrum
       ritim.py     beat tracking (comb filter + PLL)
       bolum.py     section changes
       vokal.py     vocal detection (MFCC + pitch + small MLP) and vocal loudness
  -> light engine
       isik.py      Party / Concert / Chill
       dionysos.py  Dionysos (all parameters come from the lab, analiz/lab.py)
  -> device.py      local LAN control via tinytuya, ~16 colors per second
```

`ALGORITMA.md` describes the Dionysos design in detail (in Turkish).

## Requirements

- Windows 10 or 11 (audio capture and media controls use Windows APIs).
- Python 3.11+ (developed on 3.14).
- A WiFi RGB LED strip or bulb that works with the Smart Life / Tuya app (tested with protocol 3.3).
- The PC and the strip on the same network.

## Install

```bash
git clone https://github.com/ardakarayel/music-reactive-led.git
cd music-reactive-led
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
```

## Connect your strip

1. Copy `config.example.json` to `config.json`.
2. Get your device id and local key with the tinytuya wizard. It needs a free Tuya IoT developer account; follow the [tinytuya setup guide](https://github.com/jasonacox/tinytuya#setup-wizard---getting-local-keys).

   ```bash
   .venv\Scripts\python.exe -m tinytuya wizard
   ```

3. Fill in `config.json`:

   | Field | Meaning |
   |---|---|
   | `device_id` | device id from the wizard |
   | `ip` | the strip's local IP (from the wizard or your router) |
   | `local_key` | local key from the wizard |
   | `version` | protocol version, usually `3.3` |

`config.json`, `tinytuya.json`, `devices.json`, `snapshot.json` and `tuya-raw.json` hold your keys. They are in `.gitignore`; never commit them.

Without a valid `config.json` the app starts with a **virtual device**, so you can try the UI without a strip.

## Run

- Double-click `calistir.bat` (starts without a console window), or
- `.venv\Scripts\python.exe app.py`

Closing the window quits the app. Starting it again while it is open brings the existing window to the front.

## Troubleshooting

- **"Bağlı değil" (not connected):** Is the PC on the same network? Is `ip` correct? Give the strip a fixed IP in your router. The app retries every 5 s.
- **Music mode shows no light:** Something must be playing on the default output device. The level meter in the lab shows whether audio arrives.
- **Shortcut does not work:** Another app may use `Ctrl+Alt+L`, or it needs admin rights. The tray menu always works.

## Roadmap / ideas for contributors

- Create your own **effects** from the UI (in progress).
- **OtoPilot**: a mode that moves its own sliders as the song changes (design in `ALGORITMA.md`).
- English UI, multi-strip sync, macOS/Linux audio capture.

Issues and pull requests are welcome.

## License

MIT, see `LICENSE`.
