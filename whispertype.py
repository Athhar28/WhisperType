"""WhisperType - free, offline push-to-talk dictation for Windows.

Hold the hotkey, speak, release: the text is typed into the active window.
Speech recognition runs locally (faster-whisper); no audio leaves the PC.
"""
import ctypes
import json
import logging
import os
import queue
import sys
import threading
import time
from pathlib import Path

APP = "WhisperType"
BASE = Path(__file__).resolve().parent
CONFIG_PATH = BASE / "config.json"
LOG_PATH = BASE / "whispertype.log"
SAMPLE_RATE = 16000

# pythonw.exe has no console: send stray prints/tracebacks to the log file.
_handlers = [logging.FileHandler(LOG_PATH, encoding="utf-8")]
if sys.stdout is None or sys.stderr is None:
    _logfile = open(LOG_PATH, "a", encoding="utf-8", buffering=1)
    sys.stdout = sys.stderr = _logfile
else:
    _handlers.append(logging.StreamHandler(sys.stderr))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", handlers=_handlers)
log = logging.getLogger(APP)
for _noisy in ("httpx", "huggingface_hub", "faster_whisper"):
    logging.getLogger(_noisy).setLevel(logging.WARNING)

DEFAULTS = {
    "hotkey": "ctrl+alt",        # hold to talk. e.g. "ctrl+windows", "right ctrl", "f9"
    "language": "en",            # "en", "hinglish", "hi", "auto", or any Whisper language code
    "hotkey2": "ctrl+shift",     # second hold-to-talk hotkey with its own language
    "language2": "hinglish",     # "hinglish", "hi", "en", "auto", or "off" to disable the second hotkey
    "mode": "accurate",          # "fast" = base model (quick notes), "accurate" = small model (longer text)
    "vocabulary": "",            # names / special words to help recognition, comma separated
    "sounds": True,              # beep on start / stop
    "restore_clipboard": True,   # put your previous clipboard text back after pasting
    "input_device": None,        # None = Windows default microphone
    "keep_mic_open": True,       # True = instant start, no clipped first word; False = open mic only while held
    "cpu_threads": 4,
    "beam_size": 5,              # higher = more accurate, slower (1 is fastest)
    "vad_filter": False,         # True = cut out silence with a voice detector (can clip soft words)
    "tail_ms": 400,              # keep listening this long after you release the hotkey, so last words are not cut
    "save_last_take": True,      # keep the most recent recording as last_take.wav for troubleshooting
    "hinglish_words": {},        # your own Hinglish spellings, e.g. {"किताब": "kitaab"}
}

HINDI_PROMPT = "नमस्ते, यह हिंदी में लिखा गया है।"
LANGUAGES = [("English", "en"), ("Hinglish (Hindi in English letters)", "hinglish"),
             ("Hindi (हिंदी)", "hi"), ("Auto-detect", "auto")]
LANGUAGE_NAMES = {"en": "English", "hinglish": "Hinglish", "hi": "Hindi", "auto": "Auto"}
# Fast = base model (quick notes, a few seconds). Accurate = small model (longer text, more reliable words).
MODES = [("Fast (quick notes)", "fast"), ("Accurate (longer text)", "accurate")]
MODE_LABELS = {"fast": "Fast (quick notes)", "accurate": "Accurate (longer text)"}
MODE_MODELS = {"fast": "base", "accurate": "small"}


def load_config():
    cfg = dict(DEFAULTS)
    if CONFIG_PATH.exists():
        try:
            cfg.update(json.loads(CONFIG_PATH.read_text(encoding="utf-8-sig")))  # tolerates a BOM
        except Exception:
            log.exception("config.json is invalid, using defaults")
    else:
        save_config(cfg)
    return cfg


def save_config(cfg):
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8")


# --------------------------------------------------------------------------- engine
class Engine:
    """Loads a Whisper model and turns audio into text."""

    def __init__(self):
        self.models = {}     # several can be loaded at once (one per hotkey language)
        self.name = None

    @staticmethod
    def model_name(cfg, language=None):
        language = language or cfg["language"]
        size = MODE_MODELS.get(cfg.get("mode", "accurate"), "small")
        # English-only models are more accurate for English at the same size.
        if language == "en" and size in ("tiny", "base", "small", "medium"):
            return size + ".en"
        # Tiny/base are not usable for Hindi (they even answer in Urdu script).
        if size in ("tiny", "base"):
            return "small"
        return size

    def load(self, cfg, language=None):
        name = self.name = self.model_name(cfg, language)
        if name in self.models:
            return self.models[name]
        from faster_whisper import WhisperModel

        log.info("loading model %s", name)
        t = time.time()
        kwargs = dict(device="cpu", compute_type="int8", cpu_threads=int(cfg["cpu_threads"]))
        try:
            # Already downloaded: load without touching the network.
            model = WhisperModel(name, local_files_only=True, **kwargs)
        except Exception:
            log.info("model %s not on disk yet, downloading", name)
            model = WhisperModel(name, **kwargs)
        self.models[name] = model
        log.info("model %s ready in %.1fs", name, time.time() - t)
        return model

    def transcribe(self, audio, cfg, language=None):
        language = language or cfg["language"]
        model = self.load(cfg, language)
        # Hinglish = recognise as Hindi, then convert the Devanagari to Roman letters.
        hinglish = language == "hinglish"
        lang = "hi" if hinglish else (None if language == "auto" else language)
        prompt = cfg.get("vocabulary") or None
        beam = int(cfg.get("beam_size", 5))
        if lang == "hi":
            # A Devanagari hint keeps the output in Hindi script with punctuation;
            # a wider search costs seconds here without improving the result.
            prompt = HINDI_PROMPT + (" " + prompt if prompt else "")
            beam = 1
        segments, _ = model.transcribe(
            audio,
            language=lang,
            beam_size=beam,
            vad_filter=bool(cfg.get("vad_filter", False)),
            vad_parameters={"threshold": 0.2, "min_silence_duration_ms": 700, "speech_pad_ms": 400},
            initial_prompt=prompt,
            condition_on_previous_text=False,
            without_timestamps=True,
        )
        # Drop segments Whisper itself rates as "probably not speech" or a wild
        # guess: that is what background noise turns into ("you", "Thank you.").
        kept = [s.text.strip() for s in segments
                if not (s.no_speech_prob > 0.6 and s.avg_logprob < -1.0)]
        text = " ".join(t for t in kept if t).strip()
        if hinglish and text:
            from hinglish import to_hinglish

            text = to_hinglish(text, cfg.get("hinglish_words"))
        return text


# --------------------------------------------------------------------------- audio
class Recorder:
    """Microphone capture.

    With keep_open the input stream stays open and a short pre-roll is kept in
    memory, so the first word is never clipped while the device spins up.
    Audio is only ever retained while the hotkey is held.
    """

    BLOCK = 1600  # 0.1 s

    def __init__(self, device=None, keep_open=True):
        import collections

        self.device = device
        self.keep_open = keep_open
        self.chunks = []
        self.pre = collections.deque(maxlen=6)  # ~0.6 s pre-roll, so the first word is never cut
        self.recording = False
        self.stream = None

    def open(self):
        import sounddevice as sd

        self.close()
        self.stream = sd.InputStream(samplerate=SAMPLE_RATE, channels=1, dtype="float32",
                                     blocksize=self.BLOCK, device=self.device, callback=self._callback)
        self.stream.start()

    def close(self):
        if self.stream is not None:
            try:
                self.stream.stop()
                self.stream.close()
            except Exception:
                pass
            self.stream = None

    def start(self):
        if self.stream is None or not self.stream.active:
            self.pre.clear()
            self.open()
        self.chunks = list(self.pre)
        self.recording = True

    def _callback(self, indata, frames, t, status):
        if self.recording:
            self.chunks.append(indata.copy())
        else:
            self.pre.append(indata.copy())

    def stop(self):
        import numpy as np

        self.recording = False
        if not self.keep_open:
            self.close()
        chunks, self.chunks = self.chunks, []
        if not chunks:
            return np.zeros(0, dtype="float32")
        return np.concatenate(chunks)[:, 0]


# --------------------------------------------------------------------------- overlay
class Overlay:
    """Small status pill at the bottom of the screen. Never takes keyboard focus."""

    def __init__(self):
        import tkinter as tk

        self.q = queue.Queue()
        self.root = tk.Tk()
        self.root.withdraw()
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.root.attributes("-alpha", 0.93)
        self.root.configure(bg="#1b1b1f")
        self.dot = tk.Label(self.root, text="●", font=("Segoe UI", 13), bg="#1b1b1f", fg="#ff4d4d")
        self.dot.pack(side="left", padx=(16, 6), pady=8)
        self.label = tk.Label(self.root, text="", font=("Segoe UI", 11), bg="#1b1b1f", fg="#f2f2f2")
        self.label.pack(side="left", padx=(0, 18), pady=8)
        self._hide_job = None
        self._noactivate_done = False
        self.root.after(40, self._poll)

    def show(self, text, color="#ff4d4d", hide_after=None):
        self.q.put(("show", text, color, hide_after))

    def hide(self):
        self.q.put(("hide",))

    def call(self, fn):
        self.q.put(("call", fn))

    def _poll(self):
        try:
            while True:
                msg = self.q.get_nowait()
                if msg[0] == "show":
                    self._show(*msg[1:])
                elif msg[0] == "hide":
                    self.root.withdraw()
                elif msg[0] == "call":
                    msg[1]()
        except queue.Empty:
            pass
        self.root.after(40, self._poll)

    def _show(self, text, color, hide_after):
        if self._hide_job:
            self.root.after_cancel(self._hide_job)
            self._hide_job = None
        self.label.config(text=text)
        self.dot.config(fg=color)
        self.root.update_idletasks()
        w, h = self.root.winfo_reqwidth(), self.root.winfo_reqheight()
        x = (self.root.winfo_screenwidth() - w) // 2
        y = self.root.winfo_screenheight() - h - 90
        self.root.geometry(f"{w}x{h}+{x}+{y}")
        self.root.deiconify()
        if not self._noactivate_done:
            self._set_noactivate()
        self.root.lift()
        if hide_after:
            self._hide_job = self.root.after(int(hide_after * 1000), self.root.withdraw)

    def _set_noactivate(self):
        try:
            user32 = ctypes.windll.user32
            hwnd = user32.GetParent(self.root.winfo_id()) or self.root.winfo_id()
            GWL_EXSTYLE, WS_EX_NOACTIVATE, WS_EX_TOOLWINDOW = -20, 0x08000000, 0x00000080
            style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
            user32.SetWindowLongW(hwnd, GWL_EXSTYLE, style | WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW)
            self._noactivate_done = True
        except Exception:
            log.exception("could not set no-activate style")


# --------------------------------------------------------------------------- app
class App:
    def __init__(self):
        self.cfg = load_config()
        self.engine = Engine()
        self.recorder = Recorder(self.cfg.get("input_device"), self.cfg.get("keep_mic_open", True))
        self.t_begin = 0.0
        self.overlay = Overlay()
        self.state = "loading"       # loading | idle | recording | busy
        self.cancelled = False
        self.active_keys = []
        self.active_combo = set()
        self.active_language = self.cfg["language"]
        self.last_text = ""
        self.quit_event = threading.Event()
        self.icon = None

    # ---- lifecycle
    def run(self):
        self._start_tray()
        threading.Thread(target=self._load_model, daemon=True).start()
        threading.Thread(target=self._hotkey_loop, daemon=True).start()
        self.overlay.root.mainloop()

    def _load_model(self, announce=True):
        self._set_state("loading")
        self.overlay.show("Loading speech model… (first run downloads it)", "#f5a623")
        try:
            for _, language in self._hotkeys():
                self.engine.load(self.cfg, language)
        except Exception as e:
            log.exception("model load failed")
            self.overlay.show(f"Model failed to load: {e}"[:90], "#ff4d4d", hide_after=8)
            return
        self._set_state("idle")
        if announce:
            self.overlay.show("Ready — " + self._hotkey_label(), "#4cd964", hide_after=5)
        else:
            self.overlay.hide()

    def _hotkeys(self):
        """[(hotkey, language)] - the main hotkey and, if enabled, the second one."""
        pairs = [(self.cfg["hotkey"], self.cfg["language"])]
        second = self.cfg.get("language2", "off")
        if second and second != "off" and self.cfg.get("hotkey2"):
            pairs.append((self.cfg["hotkey2"], second))
        return pairs

    def _hotkey_label(self):
        return "   |   ".join(
            "%s = %s" % (" + ".join(p.strip().title() for p in hotkey.split("+")),
                         LANGUAGE_NAMES.get(language, language))
            for hotkey, language in self._hotkeys())

    def _set_state(self, state):
        self.state = state
        if self.icon is not None:
            try:
                self.icon.icon = self._icon_image(state)
                self.icon.title = f"{APP} — {state}"
            except Exception:
                pass

    # ---- hotkey handling
    def _hotkey_loop(self):
        import keyboard

        def norm(name):
            name = (name or "").lower()
            for prefix in ("left ", "right "):
                if name.startswith(prefix):
                    name = name[len(prefix):]
            return name

        def on_event(e):
            # Any other key while holding the hotkey means it was a normal
            # shortcut (e.g. Ctrl+Alt+Del), not dictation: throw the take away.
            if self.state == "recording" and e.event_type == "down" and norm(e.name) not in self.active_combo:
                self.cancelled = True

        keyboard.hook(on_event)
        log.info("hotkeys: %s", self._hotkey_label())
        if self.recorder.keep_open:
            try:
                self.recorder.open()
            except Exception:
                log.exception("could not open the microphone at startup")

        while not self.quit_event.is_set():
            try:
                if self.state == "idle":
                    # Re-read each time so tray changes apply without a restart.
                    for hotkey, language in self._hotkeys():
                        keys = [k.strip().lower() for k in hotkey.split("+") if k.strip()]
                        if keys and all(keyboard.is_pressed(k) for k in keys):
                            self.active_keys = keys
                            self.active_combo = {norm(k) for k in keys}
                            self.active_language = language
                            self._begin()
                            break
                elif self.state == "recording":
                    if not all(keyboard.is_pressed(k) for k in self.active_keys):
                        self._finish()
            except Exception:
                log.exception("bad hotkey in config.json")
                self.overlay.show("Invalid hotkey in config.json", "#ff4d4d", hide_after=8)
                return
            time.sleep(0.015)

    def _beep(self, freq):
        if not self.cfg.get("sounds", True):
            return
        import winsound

        threading.Thread(target=winsound.Beep, args=(freq, 60), daemon=True).start()

    def _begin(self):
        self.cancelled = False
        try:
            self.recorder.start()
        except Exception as e:
            log.exception("microphone error")
            self.overlay.show(f"Microphone error: {e}"[:90], "#ff4d4d", hide_after=6)
            time.sleep(1.0)
            return
        self.t_begin = time.time()
        self._set_state("recording")
        self._beep(880)
        self.overlay.show("Listening…  (%s)" % LANGUAGE_NAMES.get(self.active_language, self.active_language),
                          "#ff4d4d")

    def _finish(self):
        import numpy as np

        # Keep recording briefly after the key is released: people often finish the word a moment later.
        if not self.cancelled:
            time.sleep(max(0, float(self.cfg.get("tail_ms", 400))) / 1000)
        audio = self.recorder.stop()
        duration = len(audio) / SAMPLE_RATE
        if self.cancelled or time.time() - self.t_begin < 0.3:
            log.info("take discarded (%s)", "another key was pressed" if self.cancelled else "too short")
            self._set_state("idle")
            self.overlay.hide()
            return
        self._set_state("busy")
        self._beep(660)
        rms = float(np.sqrt(np.mean(audio ** 2)))
        peak = float(np.abs(audio).max())
        log.info("take: %.1fs, rms %.4f, peak %.4f", duration, rms, peak)
        if self.cfg.get("save_last_take", True):
            try:
                write_wav(BASE / "last_take.wav", audio)
            except Exception:
                log.exception("could not save last_take.wav")
        if peak < 0.004:
            log.info("microphone was silent")
            self.overlay.show("No sound from the microphone", "#f5a623", hide_after=2.5)
            self._set_state("idle")
            return
        # Quiet laptop mics: bring the take up to a healthy level before recognition.
        # Level is taken from the loud part of the speech, not one stray click.
        loud = float(np.percentile(np.abs(audio), 99.8))
        gain = min(0.7 / max(loud, 1e-6), 80.0)
        if gain > 1.5:
            audio = np.clip(audio * gain, -1.0, 1.0).astype("float32")
            log.info("boosted quiet audio x%.1f", gain)
        self.overlay.show("Transcribing…", "#f5a623")
        try:
            t = time.time()
            text = self.engine.transcribe(audio, self.cfg, self.active_language)
            log.info("transcribed %.1fs of audio as %s in %.1fs (%d chars)",
                     duration, self.active_language, time.time() - t, len(text))
            if text:
                self.last_text = text
                self._type(text)
                self.overlay.hide()
            else:
                self.overlay.show("Didn't catch any speech", "#f5a623", hide_after=2.5)
        except Exception as e:
            log.exception("transcription failed")
            self.overlay.show(f"Error: {e}"[:90], "#ff4d4d", hide_after=6)
        self._set_state("idle")

    def _type(self, text):
        """Paste text into the focused window via the clipboard (works for any script)."""
        import keyboard
        import pyperclip

        old = ""
        if self.cfg.get("restore_clipboard", True):
            try:
                old = pyperclip.paste()
            except Exception:
                old = ""
        pyperclip.copy(text + " ")
        time.sleep(0.06)
        keyboard.send("ctrl+v")
        if old:
            time.sleep(0.5)
            try:
                pyperclip.copy(old)
            except Exception:
                pass

    # ---- tray
    @staticmethod
    def _icon_image(state="idle"):
        from PIL import Image, ImageDraw

        color = {"idle": "#4cd964", "recording": "#ff4d4d", "busy": "#f5a623", "loading": "#8e8e93"}.get(state, "#4cd964")
        img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        d.ellipse((2, 2, 62, 62), fill="#1b1b1f")
        d.rounded_rectangle((25, 12, 39, 38), radius=7, fill=color)          # mic body
        d.arc((17, 20, 47, 46), start=0, end=180, fill=color, width=4)       # mic cradle
        d.line((32, 46, 32, 54), fill=color, width=4)
        d.line((23, 54, 41, 54), fill=color, width=4)
        return img

    def _start_tray(self):
        import pystray
        from pystray import Menu, MenuItem as Item

        def set_language(code, key="language"):
            def handler(icon, item):
                self._change(key, code)
            return handler

        def keys_label(key):
            return " + ".join(p.strip().title() for p in str(self.cfg.get(key, "")).split("+"))

        def set_mode(mode):
            def handler(icon, item):
                self._change("mode", mode)
            return handler

        menu = Menu(
            Item(lambda item: "Hold to dictate:  " + self._hotkey_label(), None, enabled=False),
            Menu.SEPARATOR,
            Item(lambda item: "%s language" % keys_label("hotkey"), Menu(*[
                Item(label, set_language(code), radio=True,
                     checked=lambda item, code=code: self.cfg["language"] == code)
                for label, code in LANGUAGES])),
            Item(lambda item: "%s language" % keys_label("hotkey2"), Menu(*[
                Item(label, set_language(code, "language2"), radio=True,
                     checked=lambda item, code=code: self.cfg.get("language2", "off") == code)
                for label, code in LANGUAGES + [("Off (disable this hotkey)", "off")]])),
            Item(lambda item: "Mode: " + MODE_LABELS.get(self.cfg.get("mode"), "Accurate"), Menu(*[
                Item(label, set_mode(mode), radio=True,
                     checked=lambda item, mode=mode: self.cfg.get("mode", "accurate") == mode)
                for label, mode in MODES])),
            Item("Sounds", self._toggle_sounds, checked=lambda item: bool(self.cfg.get("sounds", True))),
            Item("Start with Windows", self._toggle_autostart, checked=lambda item: self._autostart_enabled()),
            Menu.SEPARATOR,
            Item("Copy last dictation", self._copy_last),
            Item("Open settings file", lambda icon, item: os.startfile(CONFIG_PATH)),
            Item("Open log", lambda icon, item: os.startfile(LOG_PATH)),
            Menu.SEPARATOR,
            Item("Quit", self._quit),
        )
        self.icon = pystray.Icon(APP, self._icon_image("loading"), APP, menu)
        self.icon.run_detached()

    def _change(self, key, value):
        if self.cfg.get(key) == value or self.state not in ("idle", "loading"):
            return
        self.cfg[key] = value
        save_config(self.cfg)
        if key == "mode":
            self.engine.models.clear()   # free the old size before loading the new one
        threading.Thread(target=self._load_model, kwargs={"announce": True}, daemon=True).start()

    def _toggle_sounds(self, icon, item):
        self.cfg["sounds"] = not self.cfg.get("sounds", True)
        save_config(self.cfg)

    def _copy_last(self, icon, item):
        if self.last_text:
            import pyperclip

            pyperclip.copy(self.last_text)

    # ---- start with Windows: a per-user "At log on" scheduled task named WhisperType.
    # (The old Run-key entry was not reliably launched at sign-in.)
    TASK = "WhisperType"

    def _autostart_enabled(self):
        import subprocess

        r = subprocess.run(["schtasks", "/Query", "/TN", self.TASK], capture_output=True,
                           creationflags=0x08000000)  # CREATE_NO_WINDOW
        return r.returncode == 0

    def _toggle_autostart(self, icon, item):
        import subprocess

        if self._autostart_enabled():
            subprocess.run(["schtasks", "/Delete", "/TN", self.TASK, "/F"], creationflags=0x08000000)
        else:
            exe = Path(sys.executable).with_name("pythonw.exe")
            cmd = f'"{exe}" "{Path(__file__).resolve()}"'
            subprocess.run(["schtasks", "/Create", "/TN", self.TASK, "/SC", "ONLOGON", "/DELAY", "0000:20",
                            "/TR", cmd, "/F"], creationflags=0x08000000)

    def _quit(self, icon=None, item=None):
        self.quit_event.set()
        try:
            self.icon.stop()
        except Exception:
            pass
        self.overlay.call(self.overlay.root.destroy)


def read_wav(path):
    """Read a 16-bit WAV as mono float32 at 16 kHz (for the --transcribe test helper)."""
    import wave

    import numpy as np

    with wave.open(str(path), "rb") as w:
        rate, channels = w.getframerate(), w.getnchannels()
        data = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype("float32") / 32768.0
    if channels > 1:
        data = data.reshape(-1, channels).mean(axis=1)
    if rate != SAMPLE_RATE:
        n = int(len(data) * SAMPLE_RATE / rate)
        data = np.interp(np.linspace(0, len(data) - 1, n), np.arange(len(data)), data).astype("float32")
    return data


def write_wav(path, audio):
    import wave

    import numpy as np

    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SAMPLE_RATE)
        w.writeframes((np.clip(audio, -1, 1) * 32767).astype(np.int16).tobytes())


def already_running():
    ctypes.windll.kernel32.CreateMutexW(None, False, "WhisperType_single_instance")
    return ctypes.windll.kernel32.GetLastError() == 183  # ERROR_ALREADY_EXISTS


def main():
    # Test helper: python whispertype.py --transcribe file.wav
    if len(sys.argv) >= 3 and sys.argv[1] == "--transcribe":
        cfg = load_config()
        engine = Engine()
        engine.load(cfg)
        audio = read_wav(sys.argv[2])
        t = time.time()
        print(engine.transcribe(audio, cfg))
        print(f"[{len(audio) / SAMPLE_RATE:.1f}s of audio transcribed in {time.time() - t:.1f}s]")
        return
    if already_running():
        ctypes.windll.user32.MessageBoxW(None, f"{APP} is already running (see the tray icon).", APP, 0x40)
        return
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass
    log.info("starting %s", APP)
    App().run()


if __name__ == "__main__":
    try:
        main()
    except Exception:
        log.exception("fatal error")
        raise
