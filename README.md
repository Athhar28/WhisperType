# WhisperType

Free, offline push-to-talk dictation for Windows. Hold a hotkey, speak, release, and the text is typed into whatever window has focus.

- **Runs locally.** Speech recognition uses [faster-whisper](https://github.com/SYSTRAN/faster-whisper). Audio never leaves your PC.
- **English, Hindi and Hinglish.** Hinglish writes Hindi speech in English letters, the way people type it.
- **Two hotkeys.** The default is Ctrl + Alt for English and Ctrl + Shift for Hinglish. Each hotkey can have its own language.
- **Runs in the tray.** Language, model, sounds and start-with-Windows are all in the tray menu.

## Requirements

- Windows 10 or 11
- Python 3.12 from [python.org](https://www.python.org/downloads/) (tick "Add python.exe to PATH" during install)
- A microphone. A headset mic gives much better results than a laptop's built-in mic.

## Install

Open PowerShell in the folder you downloaded and run:

```bash
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
```

## Run

Double-click `Start WhisperType.bat`. A microphone icon appears in the tray. The first start downloads the speech model (about 500 MB for the default `small` model), so it takes a few minutes once.

## Use

1. Click where you want the text to go.
2. Hold **Ctrl + Alt** (English) or **Ctrl + Shift** (Hinglish).
3. Speak, then release.

The pill at the bottom of the screen shows what it is doing.

## Settings

Right-click the tray icon to change languages, model size, sounds, and start-with-Windows. Other settings are in `config.json`, which is created on first run. Restart the app after editing it.

| Setting | What it does |
|---|---|
| `hotkey` / `language` | Main hotkey and its language (`en`, `hinglish`, `hi`, `auto`) |
| `hotkey2` / `language2` | Second hotkey and its language (`off` to disable) |
| `model` | `tiny`, `base`, `small`, `medium`. Larger is more accurate and slower. Tiny and base are used only for English. |
| `vocabulary` | Names or unusual words to help recognition, comma separated |
| `hinglish_words` | Your own Roman spellings for Hindi words, for example `{"किताब": "kitaab"}` |
| `vad_filter` | Cut out silence with a voice detector. Off by default because it can clip soft words. |
| `tail_ms` | How long to keep listening after you release the hotkey |
| `keep_mic_open` | Keeps the microphone open for an instant start |
| `save_last_take` | Saves your most recent recording as `last_take.wav` for troubleshooting |

## Start with Windows

Tick **Start with Windows** in the tray menu. This creates a scheduled task named *WhisperType* that starts the app 20 seconds after you sign in. Untick it to remove the task.

## Troubleshooting

- **Words are missed or wrong:** use the `small` model. Turn off Windows audio enhancements for your microphone (Settings → System → Sound → your mic → Audio enhancements → Off). Use a headset mic if you can.
- **Nothing is typed into an app:** apps running as administrator do not accept input from normal apps. Run WhisperType as administrator too, or use a normal window.
- **Check the log:** `whispertype.log` records each take, its loudness and the text produced. The tray menu has a shortcut to it.

## Privacy

Recordings are kept only in memory while you hold the hotkey. The most recent one is saved to `last_take.wav` if `save_last_take` is on, and you can turn that off. Nothing is uploaded anywhere. The only network use is downloading the speech model the first time you select it.

## Licence

MIT. See [LICENSE](LICENSE).
