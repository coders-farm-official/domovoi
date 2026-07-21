"""LED state indicators for the satellite client.

Two backends, selected by the device profile:

  * **apa102** — the 3× APA102 RGB LEDs on the ReSpeaker 2-Mics Pi HAT,
    driven per-pixel over SPI. The controller's worker thread produces
    every animation frame in software (the "thinking" chase, the error
    flash). This is the original, unchanged path.

  * **ws2812_xvf** — the 12× WS2812 ring on the ReSpeaker XVF3800 USB
    array, driven through the ``xvf_host`` CLI. The XMOS chip renders
    whole-ring effects itself (solid / breath / rainbow), so the
    controller sets a state ONCE per transition — no per-frame subprocess
    spawning. Driving a chase frame-by-frame here would fork ``xvf_host``
    dozens of times a second, and sudo + USB latency would make it stutter.

The two backends differ in who owns animation, so a driver advertises
``native_states``: when True the controller renders each state once and
blocks until the next change; when False it runs its per-pixel render +
software animation loop. Both fall back to a no-op driver if the hardware
or its control path is unavailable, so a satellite always runs.

apa102 hardware: 3× APA102 on SPI0 (GPIO 10 = MOSI, GPIO 11 = SCLK).
Enable SPI with ``sudo raspi-config nonint do_spi 0`` then reboot.
ws2812_xvf: the ``xvf_host`` binary (see satellite/PROVISIONING.md) talks
to the array over USB; commands usually need root, so a locked-down
sudoers entry lets the client run it without a password prompt.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import threading
from dataclasses import dataclass
from typing import Protocol

log = logging.getLogger("satellite.leds")


# Full-intensity RGB. The APA102 5-bit global brightness (LEDConfig.brightness)
# scales every channel down uniformly so the user can dim from the config.
COLOR_LISTENING = (0, 60, 255)   # blue — wake heard, capturing
COLOR_THINKING = (255, 100, 0)   # amber — request sent, waiting on the server
COLOR_SPEAKING = (0, 255, 80)    # green — TTS playing
COLOR_ERROR = (255, 0, 0)        # red — server error frame
COLOR_DROPIN = (255, 0, 0)       # solid red — live two-way drop-in call
COLOR_OFF = (0, 0, 0)

# ── XVF3800 WS2812 ring palette (whole-ring effects via xvf_host) ──────────
# The XVF chip only does whole-ring effects (off/breath/rainbow/single/DoA) —
# no per-pixel control — so states map to those:
#   listening  DoA    — green dot toward your voice, dim BLUE background
#   thinking   breath — yellow pulse
#   speaking   breath — blue pulse
#   music      rainbow— lively rotating colors while music plays
#   error      single — solid red
# IMPORTANT: DoA only renders when there's live mic sound, so it's usable
# ONLY for listening (when *you're* talking). During thinking the room is
# usually quiet, and during speaking the chip's AEC removes the bot's own
# voice from the mic — in both cases DoA freezes on its last frame. So those
# states use breath, not DoA. `led_doa_color` takes (background, dot);
# led_brightness applies to breath/rainbow only, so the DoA colors carry
# their own level (dim background wash + brighter dot).
XVF_LISTEN_BASE = (0, 0, 170)    # blue background while listening (DoA isn't
                                 # affected by led_brightness, so its level
                                 # lives here in the color value)
XVF_LISTEN_DOT = (0, 255, 0)     # green dot toward your voice (already full)
XVF_SPEAK = (0, 60, 255)         # blue pulse while speaking
# Breath color for "thinking" — yellow (red+green at full renders true,
# sidestepping the WS2812 orange-reads-as-red issue).
XVF_THINK = (255, 255, 0)
# led_speed: small ints (the xvf_host example uses 1). Exact scale/direction
# is firmware-defined — tune these if breath/rainbow feel too fast or slow.
XVF_SPEED_BREATH = 1             # slow pulse while thinking / speaking
XVF_SPEED_RAINBOW = 3            # rotating rainbow while music plays


def _hex(color: tuple[int, int, int]) -> str:
    r, g, b = color
    return f"0x{r & 0xFF:02x}{g & 0xFF:02x}{b & 0xFF:02x}"


@dataclass
class LEDConfig:
    enabled: bool = True
    num_leds: int = 3
    brightness: int = 16  # apa102: 0-31 (5-bit global); ws2812_xvf: 0-255
    backend: str = "apa102"  # "apa102" | "ws2812_xvf"
    xvf_host_path: str = "xvf_host"  # ws2812_xvf only


class _Driver(Protocol):
    # True  → driver renders whole states itself (set-once; hardware may
    #         self-animate). The controller never produces frames.
    # False → driver is per-pixel; the controller drives solids and the
    #         software animation loop via set_pixel/show.
    native_states: bool

    def render_state(self, state: str) -> None: ...  # native_states=True path
    def set_pixel(self, idx: int, r: int, g: int, b: int) -> None: ...  # pixel path
    def show(self) -> None: ...
    def clear(self) -> None: ...
    def close(self) -> None: ...


class _NoOpDriver:
    native_states = False

    def render_state(self, state: str) -> None: pass
    def set_pixel(self, idx: int, r: int, g: int, b: int) -> None: pass
    def show(self) -> None: pass
    def clear(self) -> None: pass
    def close(self) -> None: pass


class _APA102Driver:
    """APA102 frames over SPI.

    Wire format: 32-bit start frame of zeros, one 4-byte LED frame per LED
    (header byte ``0xE0 | brightness``, then BGR — not RGB), then enough
    1-bits to clock the data through the daisy chain. ``ceil(N/16)`` bytes
    of ``0xFF`` rounds the trailing clocks up to a byte boundary.
    """

    native_states = False

    def __init__(self, num_leds: int, brightness: int) -> None:
        import spidev

        self._n = num_leds
        self._brightness = max(0, min(31, brightness))
        self._pixels: list[tuple[int, int, int]] = [(0, 0, 0)] * num_leds
        self._spi = spidev.SpiDev()
        # /dev/spidev0.0 = SPI0 bus, CE0 device. Raises FileNotFoundError
        # if SPI isn't enabled in raspi-config.
        self._spi.open(0, 0)
        # 8 MHz is well under APA102's tolerance and rock-solid on the Pi
        # Zero 2 W's GPIO. The 3 onboard LEDs are millimeters from the SPI
        # pins so signal integrity isn't a concern.
        self._spi.max_speed_hz = 8_000_000

    def render_state(self, state: str) -> None:  # pragma: no cover - unused (pixel path)
        pass

    def set_pixel(self, idx: int, r: int, g: int, b: int) -> None:
        if 0 <= idx < self._n:
            self._pixels[idx] = (r & 0xFF, g & 0xFF, b & 0xFF)

    def show(self) -> None:
        head = 0xE0 | self._brightness
        buf: list[int] = [0, 0, 0, 0]  # start frame
        for r, g, b in self._pixels:
            buf.extend([head, b, g, r])  # APA102 wire order is BGR
        end_bytes = max(1, (self._n + 15) // 16)
        buf.extend([0xFF] * end_bytes)
        self._spi.xfer2(buf)

    def clear(self) -> None:
        self._pixels = [(0, 0, 0)] * self._n
        self.show()

    def close(self) -> None:
        try:
            self.clear()
        except Exception:
            pass
        try:
            self._spi.close()
        except Exception:
            pass


class _WS2812XvfDriver:
    """12× WS2812 ring on the XVF3800 via the ``xvf_host`` CLI.

    xvf_host exposes whole-ring effects, not per-pixel control:
        led_effect      <0=off|1=breath|2=rainbow|3=single|4=DoA>
        led_color       0xRRGGBB         (breath / single-color)
        led_doa_color   <base> <dot>     (DoA mode: resting + moving-dot color)
        led_speed       <n>              (breath / rainbow animation rate)
        led_brightness  <0-255>          (breath / rainbow)
    Each call is a process spawn, so this driver is ``native_states`` — the
    controller sets a state once and the chip animates breath/rainbow/DoA itself.

    Commands usually need root. We probe plain invocation first (in case a
    udev rule grants USB access) and fall back to ``sudo -n`` (matching the
    locked-down sudoers entry in PROVISIONING). Any probe failure raises so
    ``_make_driver`` falls back to the no-op driver — a satellite without a
    working control path still runs, just dark.
    """

    native_states = True

    # Map satellite state → an xvf effect + its parameters. Keys per spec:
    #   effect : led_effect id (0=off 1=breath 2=rainbow 3=single 4=DoA)
    #   doa    : (base_color, dot_color) for DoA mode (led_doa_color)
    #   color  : led_color for breath / single-color modes
    #   speed  : led_speed for breath / rainbow modes
    _STATES = {
        "idle":      {"effect": 0},
        "listening": {"effect": 4, "doa": (XVF_LISTEN_BASE, XVF_LISTEN_DOT)},  # green dot, blue bg
        "thinking":  {"effect": 1, "color": XVF_THINK, "speed": XVF_SPEED_BREATH},  # yellow pulse
        "speaking":  {"effect": 1, "color": XVF_SPEAK, "speed": XVF_SPEED_BREATH},  # blue pulse
        "music":     {"effect": 2, "speed": XVF_SPEED_RAINBOW},                # rotating rainbow
        "error":     {"effect": 3, "color": COLOR_ERROR},                      # solid red
        "dropin":    {"effect": 3, "color": COLOR_DROPIN},                     # solid red — live call
    }

    def __init__(self, brightness: int, xvf_host_path: str) -> None:
        path = shutil.which(xvf_host_path) or xvf_host_path
        self._path = path
        self._brightness = max(0, min(255, brightness))
        self._prefix = self._probe(path)  # raises if no working invocation
        # Assert brightness once up front; per-state renders only touch
        # effect + color so the ring isn't re-dimmed on every transition.
        self._run("led_brightness", str(self._brightness))

    def _probe(self, path: str) -> list[str]:
        if shutil.which(path) is None and not path.startswith(("/", "./")):
            raise FileNotFoundError(f"xvf_host not found on PATH: {path!r}")
        # Try plain, then sudo -n. `sudo -n` fails fast (no hang) when no
        # passwordless rule exists, so neither branch blocks on a prompt.
        for prefix in ([path], ["sudo", "-n", path]):
            try:
                rc = subprocess.run(
                    [*prefix, "led_brightness", str(self._brightness)],
                    capture_output=True, text=True, timeout=5.0,
                ).returncode
            except (FileNotFoundError, subprocess.TimeoutExpired):
                continue
            if rc == 0:
                log.info("xvf_host LED control via: %s", " ".join(prefix))
                return prefix
        raise RuntimeError(
            f"xvf_host present at {path!r} but no working invocation "
            "(plain or `sudo -n`) — check the sudoers entry from PROVISIONING."
        )

    def _run(self, *args: str) -> None:
        """Fire one xvf_host command, best-effort. Never raises — a failed
        LED write must not take down the LED worker thread (which would
        leave the ring stuck) nor bubble into the audio path."""
        try:
            r = subprocess.run(
                [*self._prefix, *args],
                capture_output=True, text=True, timeout=5.0,
            )
            if r.returncode != 0:
                log.debug("xvf_host %s rc=%d: %s", args, r.returncode, r.stderr.strip()[:120])
        except (FileNotFoundError, subprocess.TimeoutExpired) as e:
            log.debug("xvf_host %s failed: %s", args, e)

    def render_state(self, state: str) -> None:
        spec = self._STATES.get(state, self._STATES["idle"])
        effect = spec["effect"]
        if effect == 0:
            self._run("led_effect", "0")
            return
        # Set colors/speed before the effect so it starts already configured.
        if "doa" in spec:
            base, dot = spec["doa"]
            self._run("led_doa_color", _hex(base), _hex(dot))
        if "color" in spec:
            self._run("led_color", _hex(spec["color"]))
        if "speed" in spec:
            self._run("led_speed", str(spec["speed"]))
        self._run("led_effect", str(effect))

    # Per-pixel protocol members — never invoked (native_states=True).
    def set_pixel(self, idx: int, r: int, g: int, b: int) -> None: pass
    def show(self) -> None: pass

    def clear(self) -> None:
        self._run("led_effect", "0")

    def close(self) -> None:
        self.clear()


def _make_driver(cfg: LEDConfig) -> _Driver:
    if cfg.backend == "ws2812_xvf":
        try:
            return _WS2812XvfDriver(cfg.brightness, cfg.xvf_host_path)
        except FileNotFoundError:
            log.warning(
                "xvf_host not found (%r) — XVF3800 LED ring disabled for this "
                "run. Install it per PROVISIONING or set [leds] enabled = false.",
                cfg.xvf_host_path,
            )
            return _NoOpDriver()
        except Exception as e:
            log.warning("xvf_host LED init failed (%s: %s); LEDs disabled", type(e).__name__, e)
            return _NoOpDriver()

    # Default backend: APA102 over SPI.
    try:
        return _APA102Driver(cfg.num_leds, cfg.brightness)
    except ImportError:
        log.info("spidev not installed; LED indicators disabled")
        return _NoOpDriver()
    except FileNotFoundError:
        log.warning(
            "/dev/spidev0.0 missing — enable SPI with "
            "`sudo raspi-config nonint do_spi 0` then reboot. "
            "LED indicators disabled for this run."
        )
        return _NoOpDriver()
    except PermissionError:
        log.warning(
            "permission denied opening /dev/spidev0.0 — add the satellite "
            "user to the `spi` group (`sudo usermod -aG spi $USER`) then "
            "log out + back in. LED indicators disabled for this run."
        )
        return _NoOpDriver()
    except Exception as e:
        log.warning("APA102 init failed (%s: %s); LEDs disabled", type(e).__name__, e)
        return _NoOpDriver()


class LEDController:
    """Renders satellite state on the LEDs from a worker thread.

    Callers invoke ``set_state(name)`` from any thread; the worker reads
    the current state and renders it. For per-pixel backends the worker
    blocks on the change event for solids and ticks a timer for the
    thinking chase / error flash. For native-state backends it sets each
    state once and blocks until the next transition (the hardware animates).
    """

    def __init__(self, cfg: LEDConfig) -> None:
        self._cfg = cfg
        self._state: str = "idle"
        self._lock = threading.Lock()
        self._changed = threading.Event()
        self._stop = threading.Event()
        self._driver: _Driver = _NoOpDriver()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if not self._cfg.enabled:
            log.info("[leds] enabled = false; running without indicators")
            return
        self._driver = _make_driver(self._cfg)
        self._thread = threading.Thread(
            target=self._run, daemon=True, name="leds"
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._changed.set()
        if self._thread is not None:
            self._thread.join(timeout=1.0)
        try:
            self._driver.close()
        except Exception:
            pass

    def set_state(self, state: str) -> None:
        """Unconditionally transition to ``state``."""
        with self._lock:
            if state == self._state:
                return
            log.debug("[leds] %s -> %s", self._state, state)
            self._state = state
            self._changed.set()

    def set_state_unless(self, unless: str, new: str) -> None:
        """Transition to ``new`` unless the current state is ``unless``.

        Avoids a race where a delayed ``response_end`` from the receiver
        thread downgrades a ``listening`` state the mic thread already
        set in response to barge-in.
        """
        with self._lock:
            if self._state == unless or self._state == new:
                return
            log.debug("[leds] %s -> %s (unless=%s)", self._state, new, unless)
            self._state = new
            self._changed.set()

    def _current_state(self) -> str:
        with self._lock:
            return self._state

    def _solid(self, color: tuple[int, int, int]) -> None:
        for i in range(self._cfg.num_leds):
            self._driver.set_pixel(i, *color)
        self._driver.show()

    def _run(self) -> None:
        try:
            if self._driver.native_states:
                self._run_native()
            else:
                self._run_pixel()
        except Exception:
            log.exception("LED thread crashed")
        finally:
            try:
                if self._driver.native_states:
                    self._driver.render_state("idle")
                else:
                    self._solid(COLOR_OFF)
            except Exception:
                pass

    def _run_native(self) -> None:
        """Set-once rendering for hardware that animates its own effects."""
        while not self._stop.is_set():
            self._changed.clear()
            self._driver.render_state(self._current_state())
            self._changed.wait()

    def _run_pixel(self) -> None:
        """Per-pixel rendering + software animation (APA102)."""
        while not self._stop.is_set():
            self._changed.clear()
            state = self._current_state()
            if state == "idle":
                self._solid(COLOR_OFF)
                self._changed.wait()
            elif state == "listening":
                self._solid(COLOR_LISTENING)
                self._changed.wait()
            elif state == "speaking":
                self._solid(COLOR_SPEAKING)
                self._changed.wait()
            elif state == "dropin":
                # Solid red for the duration of a live drop-in call.
                self._solid(COLOR_DROPIN)
                self._changed.wait()
            elif state == "thinking":
                self._tick_thinking()
            elif state == "error":
                self._tick_error()
            else:
                self._solid(COLOR_OFF)
                self._changed.wait()

    def _tick_thinking(self) -> None:
        n = self._cfg.num_leds
        i = 0
        period = 0.18
        r, g, b = COLOR_THINKING
        while not self._stop.is_set() and self._current_state() == "thinking":
            for j in range(n):
                if j == i:
                    self._driver.set_pixel(j, r, g, b)
                else:
                    # 1/8-intensity trail keeps every LED faintly lit so
                    # the chase reads as motion, not as a single pixel
                    # blinking.
                    self._driver.set_pixel(j, r // 8, g // 8, b // 8)
            self._driver.show()
            i = (i + 1) % n
            self._changed.wait(timeout=period)

    def _tick_error(self) -> None:
        for _ in range(3):
            if self._stop.is_set() or self._current_state() != "error":
                return
            self._solid(COLOR_ERROR)
            if self._changed.wait(timeout=0.15):
                return
            self._solid(COLOR_OFF)
            if self._changed.wait(timeout=0.15):
                return
        # Demote internally so the next loop iteration renders idle. If a
        # caller set a different state during the flash, it took the lock
        # first and we'd have returned via the early-exit checks above.
        with self._lock:
            if self._state == "error":
                self._state = "idle"
                self._changed.set()
