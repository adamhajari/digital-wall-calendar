"""Command line: `python -m wallcal <command>`.

  run        start the calendar (sync loop + panel + web control panel)
  render     draw one month to a PNG (or to the panel) and exit
  auth       sign in to Google with OAuth (only for auth = "oauth")
  calendars  list the calendars your Google credentials can see
"""

from __future__ import annotations

import argparse
import importlib.util
import logging
import signal
import subprocess
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

from .config import PROJECT_ROOT, load_config


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m wallcal", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--config", type=Path, default=None,
                        help="config file (default: config.toml in the project folder, if present)")
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="run the calendar")
    run.add_argument("--sample", action="store_true", help="use made-up events instead of Google")
    run.add_argument("--preview", action="store_true", help="don't drive the panel; write data/panel.png")
    run.add_argument("--dev", action="store_true",
                     help="laptop development: --preview, plus reload the renderer when files change")
    run.add_argument("--port", type=int, help="web server port")

    rend = sub.add_parser("render", help="render one month to a PNG and exit")
    rend.add_argument("--sample", action="store_true", help="use made-up events instead of Google")
    rend.add_argument("--month", help="YYYY-MM (default: current month)")
    rend.add_argument("--today", help="pretend today is YYYY-MM-DD")
    rend.add_argument("-o", "--out", type=Path, default=Path("preview.png"))
    rend.add_argument("--raw", action="store_true", help="pure panel colors instead of simulated ink")
    rend.add_argument("--to-panel", action="store_true", help="also show it on the Inky panel")

    sub.add_parser("auth", help="sign in to Google (OAuth); run this on a computer with a browser")
    sub.add_parser("calendars", help="list calendars visible to your Google credentials")

    args = parser.parse_args()
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    for noisy in ("urllib3", "google", "PIL"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    config_path = args.config
    if config_path is None and (PROJECT_ROOT / "config.toml").exists():
        config_path = PROJECT_ROOT / "config.toml"
    cfg = load_config(config_path)
    logging.info("Config: %s", config_path or "(defaults: sample data, preview display)")

    if getattr(args, "sample", False):
        cfg.source = "sample"
    if getattr(args, "dev", False) or getattr(args, "preview", False):
        cfg.backend = "preview"
        cfg.buttons = False
        cfg.min_refresh_seconds = 0
        cfg.debounce_seconds = 0.5

    {"run": cmd_run, "render": cmd_render, "auth": cmd_auth, "calendars": cmd_calendars}[args.command](cfg, args)


def cmd_run(cfg, args) -> None:
    from .buttons import Button, Combo, start_buttons
    from .controller import Controller
    from .display import make_display
    from .settings import SettingsStore
    from .sources import make_source
    from .web import create_app, serve

    display = make_display(cfg.backend, cfg.data_dir, cfg.panel_command_delay, cfg.panel_power_command_delay)
    ctl = Controller(cfg, make_source(cfg), display,
                     SettingsStore(cfg.data_dir / "settings.json"), dev=args.dev)
    app = create_app(ctl)
    # Optional photo mode: delete wallcal/photos/ to remove it. (Checking .origin skips a
    # leftover folder holding only __pycache__, which would import as an empty package.)
    if (spec := importlib.util.find_spec(f"{__package__}.photos")) and spec.origin:
        from .photos import install as install_photos

        install_photos(ctl, app, cfg)
    ctl.start()
    if cfg.buttons:
        def prev(): ctl.navigate("prev")
        def next_(): ctl.navigate("next")
        def today(): ctl.navigate("today")
        def sync(): ctl.request_sync(); ctl.force_refresh()

        # Onboard A-D, then the optional frame buttons: press / hold.
        onboard = [Button(p, n, f) for p, n, f in zip(cfg.button_pins, "ABCD", [prev, next_, today, sync])]
        frame = [Button(p, n, f, h) for p, n, f, h in
                 zip(cfg.frame_button_pins, ["left", "right"], [prev, next_], [today, sync])]
        combos = []
        if len(frame) == 2 and cfg.power_off_hold_seconds > 0:
            i = len(onboard)
            combos.append(Combo((i, i + 1), "left+right", cfg.power_off_hold_seconds, lambda: power_off(ctl)))
        start_buttons(onboard + frame, cfg.hold_seconds, combos)
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))  # clean exit under systemd
    port = args.port or cfg.port
    print(f"\n  Control panel: http://localhost:{port}\n", flush=True)
    serve(app, cfg.host, port)


def power_off(ctl) -> None:
    """Shut the Pi down, once any panel refresh in progress has finished.

    Cutting power mid-refresh can leave the panel stuck until it's unplugged. Needs passwordless
    sudo, which Raspberry Pi OS gives its first user.
    """
    deadline = time.monotonic() + 90
    while ctl.display_state == "refreshing" and time.monotonic() < deadline:
        logging.info("Waiting for the panel refresh to finish before powering off")
        time.sleep(2)
    logging.warning("Powering off")
    result = subprocess.run(["sudo", "-n", "systemctl", "poweroff"], capture_output=True, text=True)
    if result.returncode:
        logging.error("Power off failed: %s", result.stderr.strip() or result.returncode)


def cmd_render(cfg, args) -> None:
    from . import palette
    from .controller import add_months
    from .display import make_display, to_panel_orientation
    from .render import RenderInput, render
    from .settings import SettingsStore
    from .sources import make_source

    today = date.fromisoformat(args.today) if args.today else datetime.now(cfg.tz).date()
    y, m = (int(p) for p in args.month.split("-")) if args.month else (today.year, today.month)
    settings = SettingsStore(cfg.data_dir / "settings.json").get()
    hidden = set(settings.hidden_calendars)

    source = make_source(cfg)
    ny, nm = add_months(y, m, 1)
    events = source.fetch(date(y, m, 1) - timedelta(days=7), date(ny, nm, 1) + timedelta(days=14))
    calendars = [c for c in source.calendars if c.id not in hidden]
    for c in calendars:
        c.color = settings.calendar_colors.get(c.id, c.color)

    img = render(RenderInput(y, m, today, events, calendars, settings))
    (img if args.raw else palette.simulate(img)).save(args.out)
    print(f"Wrote {args.out} ({len(events)} events)")
    if args.to_panel:
        print("Updating panel (takes ~30-40 seconds)...")
        display = make_display("inky", cfg.data_dir, cfg.panel_command_delay, cfg.panel_power_command_delay)
        display.show(to_panel_orientation(img, settings.orientation))


def cmd_auth(cfg, args) -> None:
    from .sources import run_oauth_flow

    run_oauth_flow(cfg)


def cmd_calendars(cfg, args) -> None:
    from .sources import list_calendars

    items = list_calendars(cfg)
    if not items:
        print("No calendars in this account's list. (A service account only sees calendars you\n"
              "shared with it; add their IDs to config.toml directly.)")
    for c in items:
        print(f"{c['id']}\n    {c.get('summary', '')}  [{c.get('accessRole', '')}]")


if __name__ == "__main__":
    main()
