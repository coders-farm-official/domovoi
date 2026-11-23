"""The support-session light (satellite/leds.py): magenta #FF00C8 on a slow
4-second breath, an overlay that state changes and resync() keep; on the
XVF3800 the chip's breath while idle and DoA mode (the state's colour as
the base, a magenta dot) otherwise; a drop-in call's red wins. The real
colours and pace still need a check on hardware (noted in the code)."""

from __future__ import annotations

from satellite import leds


def test_the_colour_and_the_pace() -> None:
    assert leds.COLOR_SUPPORT == (0xFF, 0x00, 0xC8)
    assert leds.SUPPORT_BREATH_SEC == 4.0 and leds.SUPPORT_BREATH_MIN == 0.10
    # Not violet's (#A855F7) awaiting-approval colour.
    assert leds.COLOR_SUPPORT != leds.SETUP_COLORS["awaiting-approval"]
    src = open(leds.__file__, encoding="utf-8").read()
    assert src.count("NEEDS A CHECK ON REAL HARDWARE") >= 2


def test_the_overlay_survives_state_changes_and_resync() -> None:
    c = leds.LEDController(leds.LEDConfig(enabled=False))
    c.set_support(True)
    for state in ("listening", "thinking", "idle"):
        c.set_state(state)
    c.resync()
    assert c.support
    c.set_support(False)
    assert not c.support


def test_apa102_breathes_when_idle_and_holds_led_0_otherwise() -> None:
    c = leds.LEDController(leds.LEDConfig(enabled=False, num_leds=3))
    levels = []
    for i in range(41):
        px = c.support_pixels("idle", i * 0.1)
        assert len(px) == 3 and len(set(px)) == 1
        levels.append(px[0][0] / 255)
    assert min(levels) == leds.SUPPORT_BREATH_MIN or abs(min(levels) - 0.1) < 0.01
    assert max(levels) > 0.99
    # 2 s up, 2 s down.
    assert levels.index(max(levels)) == 20
    px = c.support_pixels("listening", 0)
    assert px[0] == leds.COLOR_SUPPORT and px[1:] == [leds.COLOR_LISTENING] * 2


class _Recorder(leds._WS2812XvfDriver):
    def __init__(self) -> None:      # no hardware
        self.calls: list[tuple[str, ...]] = []

    def _run(self, *args: str) -> None:
        self.calls.append(args)


def test_xvf_breath_dot_and_dropin() -> None:
    d = _Recorder()
    d.render_state("idle", support=True)
    assert d.calls == [("led_color", "0xff00c8"), ("led_speed", str(leds.XVF_SPEED_SUPPORT)),
                       ("led_effect", "1")]
    assert leds.XVF_SPEED_SUPPORT <= 1           # the chip's slowest; violet's is 2
    d.calls.clear()
    d.render_state("speaking", support=True)
    assert d.calls == [("led_doa_color", leds._hex(leds.XVF_SUPPORT_BASE["speaking"]), "0xff00c8"),
                       ("led_effect", "4")]
    d.calls.clear()
    d.render_state("dropin", support=True)       # a call's solid red wins
    assert ("led_color", leds._hex(leds.COLOR_DROPIN)) in d.calls and ("led_effect", "3") in d.calls
    d.calls.clear()
    d.render_state("idle")                        # without a session: off, as before
    assert d.calls == [("led_effect", "0")]
