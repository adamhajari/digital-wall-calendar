# wallcal: an e-ink wall calendar

Shows a month of Google Calendar events on a **Pimoroni Inky Impression 13.3" (2025 Edition, Spectra 6)**,
driven by a **Raspberry Pi Zero 2 W**, designed to look like a printed paper wall calendar.

- Checks Google Calendar every 60 seconds and redraws the panel only when something visible changed
  (a new or edited event, the date, settings or the month being shown).
- Runs a control panel on your network at `http://wallcal.local:8080`. You can browse to other months,
  change orientation, colors, week start and which calendars show, force a sync, or redraw the panel.
- Always shows the current month by default. If you browse to another month, it goes back to the current
  month after 30 minutes. You can change that in the control panel.
- The panel's side buttons: **A** previous month, **B** next month, **C** today, **D** sync and redraw.
  You can also add [two buttons on the frame](docs/frame-buttons.md).
- On a laptop, it runs the same code with a **live preview** instead of the panel.

```
wallcal/            the app (python package)
  render.py         draws the calendar image  <- most design tweaks happen here
  controller.py     sync loop, change detection, panel refresh scheduling
  sources.py        Google Calendar + sample data
  display.py        Inky panel / preview backends
  web.py, templates/index.html   control panel
  fonts/            bundled OFL fonts
config.example.toml copy to config.toml
deploy/             systemd unit
scripts/            install-service.sh (on the Pi), deploy.sh (from your laptop)
docs/               optional hardware guides
```

---

## 0. Preview on your laptop first

You need Python 3.11 or newer.

```bash
cd digital_wall_calendar
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

.venv/bin/python -m wallcal run --dev
```

Open <http://localhost:8080>. With no `config.toml` it uses **sample events**. The image in the page
uses approximate real ink colors, so it shows roughly what the panel will look like.

`--dev` reloads the renderer whenever you save a `.py` file in `wallcal/`. Edit `render.py`, save, and
the preview updates within a second. A copy of the "panel" image is also written to `data/panel.png`.

Other useful commands:

```bash
# Render a single month to a PNG and exit
.venv/bin/python -m wallcal render --month 2026-12 --today 2026-12-25 -o preview.png
# Same, but with pure panel colors instead of simulated ink
.venv/bin/python -m wallcal render --raw -o raw.png
# Use your real Google data in the preview (after step 2), forcing preview mode:
.venv/bin/python -m wallcal run --dev            # with a config.toml that has source = "google"
```

---

## 1. Set up the Pi

### Hardware

1. Your Pi Zero 2 W needs a **soldered 40-pin header**. Buy the "Zero 2 WH" or solder one on.
2. With the power off, plug the Pi onto the header on the back of the Inky Impression. The pins face
   into the Inky's socket and the Pi's board sits over the back of the display.
3. Power the Pi through its **PWR IN** micro-USB port (the one at the edge, not the middle one) with a
   good 5V 2.5A supply.

### Flash the SD card

1. Install **Raspberry Pi Imager** on your laptop: <https://www.raspberrypi.com/software/>.
2. Choose:
   - Device: **Raspberry Pi Zero 2 W**
   - OS: **Raspberry Pi OS (other) → Raspberry Pi OS Lite (64-bit)**. You don't need a desktop.
3. Click **Edit settings** (OS customisation) and set:
   - Hostname: `wallcal`
   - Username `pi` and a password. The deploy script assumes `pi@wallcal.local`; you can pass another.
   - Your Wi-Fi name, password and country. The Zero 2 W only supports **2.4 GHz** Wi-Fi.
   - Locale and **timezone**. The calendar uses the Pi's timezone unless you set one in `config.toml`.
   - Services tab: **Enable SSH** (password, or better, your public key).
4. Write the card, put it in the Pi and power it on. The first boot takes a few minutes.

### Configure the OS

From your laptop:

```bash
ssh pi@wallcal.local
```

On the Pi:

```bash
sudo apt update && sudo apt full-upgrade -y
sudo apt install -y git python3-venv python3-dev build-essential

# Turn on the SPI and I2C interfaces the Inky uses
sudo raspi-config nonint do_spi 0
sudo raspi-config nonint do_i2c 0

# The Inky drives its own chip-select pins, so free them from the SPI driver
echo "dtoverlay=spi0-0cs" | sudo tee -a /boot/firmware/config.txt

sudo reboot
```

Without the `spi0-0cs` line, the Inky library fails with
`Woah there, some pins we need are in use!` / `Chip Select: (line 8, GPIO8) currently claimed by spi0 CS0`.

---

## 2. Connect Google Calendar

Google's "push" notifications only work with a public HTTPS server. A Pi on your home network can't
receive them, so the Pi asks Google for changes every 60 seconds (`poll_seconds`) instead. That uses
a tiny fraction of the free API quota. A new event shows up on the wall within about a minute, plus
around 30 to 40 seconds for the e-ink refresh.

There are two ways to sign in. **Use a service account.** It is a robot Google account with a key file
that never expires, which suits a device on the wall with no screen.

### Option A: service account (recommended)

1. Go to <https://console.cloud.google.com/>, sign in, and **create a project** (e.g. "Wall Calendar").
2. **APIs & Services → Library**, search for **Google Calendar API** and click **Enable**.
3. **IAM & Admin → Service Accounts → Create service account**. Name it `wallcal`, then click
   *Done*. It doesn't need any roles.
4. Click the new service account, then **Keys → Add key → Create new key → JSON**. A file downloads.
   Save it in this project as **`secrets/service_account.json`**. The `secrets/` folder is gitignored.
   Keep the file private; anyone with it can read the calendars you share with it.
5. Copy the service account's email address. It looks like
   `wallcal@your-project.iam.gserviceaccount.com`.
6. In **Google Calendar on the web**, open ⚙ **Settings**. Under *Settings for my calendars*, click each
   calendar you want on the wall:
   - **Share with specific people or groups → Add people** → paste the service account email →
     permission **See all event details** → Send.
   - Scroll to **Integrate calendar** and copy the **Calendar ID**. Your main calendar's ID is your
     Gmail address.
   - A calendar someone else owns, like a partner's, has to be shared with the service account by
     its owner. You can also use Option B for those.
7. Create your config and list the calendars:
   ```bash
   cp config.example.toml config.toml
   # edit config.toml: add a [[calendars]] block per calendar ID, pick colors
   ```
   The example config includes the US holidays calendar. It is public, so it works without sharing,
   and appears in small italics next to the date. Other countries use IDs like
   `en.uk#holiday@group.v.calendar.google.com`.
8. Test it on your laptop:
   ```bash
   .venv/bin/python -m wallcal render -o preview.png     # should say "Wrote preview.png (N events)"
   .venv/bin/python -m wallcal run --dev                 # live preview with your real events
   ```
   A `404 ... not found` error means the calendar isn't shared with the service account yet, or the
   ID is wrong. Sharing can take a few minutes to take effect.

### Option B: OAuth (sign in as yourself)

Use this if you'd rather not share calendars with a service account. It can read anything in your own
calendar list.

1. Do steps 1 and 2 above.
2. **APIs & Services → OAuth consent screen**: user type **External**. Fill in the app name and your
   email, and add yourself as a **test user**.
3. Then **Publish app → In production**. This step matters. Apps left in "Testing" get tokens that
   **expire after 7 days**, and the calendar would quietly stop syncing. You won't go through
   verification; Google just shows an "unverified app" warning when you sign in, which you can click past.
4. **Credentials → Create credentials → OAuth client ID → Desktop app**. Download the JSON and save
   it as **`secrets/client_secret.json`**.
5. In `config.toml`, set `auth = "oauth"` and uncomment the two `oauth_*` lines.
6. Sign in **on your laptop**, which has a browser:
   ```bash
   .venv/bin/python -m wallcal auth        # opens a browser; writes secrets/token.json
   .venv/bin/python -m wallcal calendars   # lists calendar IDs you can use in config.toml
   ```
7. Later you'll copy `secrets/token.json` to the Pi (step 3).

---

## 3. Get the code running on the Pi

From your **laptop**, in this project folder:

```bash
# Copy the code over (also creates the virtualenv and installs packages on the Pi.
# The first install takes several minutes on a Zero 2 W.)
scripts/deploy.sh                     # or: scripts/deploy.sh pi@<ip-address>

# Copy your config and Google credentials. These are deliberately never synced by deploy.sh.
ssh pi@wallcal.local 'mkdir -p ~/wallcal/secrets'
scp config.toml pi@wallcal.local:~/wallcal/config.toml
scp secrets/* pi@wallcal.local:~/wallcal/secrets/
```

On the Pi, make sure its `config.toml` has `backend = "inky"` and `buttons = true`, as in the example.
Then try it in the foreground:

```bash
ssh pi@wallcal.local
cd ~/wallcal

# One-off: draw this month on the panel and exit (refresh takes ~30-40 s)
.venv/bin/python -m wallcal render --to-panel -o /tmp/preview.png

# The full app; stop with Ctrl+C
.venv/bin/python -m wallcal run
```

Open <http://wallcal.local:8080> from your phone or laptop. If `.local` names don't resolve on your
network, use the Pi's IP address (`hostname -I` on the Pi).

If you prefer git: push this folder to a repository and `git clone` it to `~/wallcal` on the Pi.
Then create the venv with `python3 -m venv .venv && .venv/bin/pip install -r requirements-pi.txt`.

---

## 4. Start automatically on boot

On the Pi:

```bash
~/wallcal/scripts/install-service.sh
```

This installs a **systemd** service (`deploy/wallcal.service`) that starts the calendar at boot once
the network is up, and restarts it 10 seconds after a crash.

```bash
systemctl status wallcal           # is it running?
journalctl -u wallcal -f           # live logs
journalctl -u wallcal -b           # logs since boot
sudo systemctl restart wallcal
sudo systemctl disable --now wallcal   # stop it starting on boot
```

To test it, run `sudo reboot`. The control panel should come back within a minute or so.

---

## 5. Optional: buttons on the frame

If a matboard hides the panel's own buttons, you can add two buttons on the side of the frame. See
[docs/frame-buttons.md](docs/frame-buttons.md). No soldering needed.

---

## Day-to-day development workflow

1. Change code on your laptop and watch it with `.venv/bin/python -m wallcal run --dev`.
2. When it looks right, run `scripts/deploy.sh`. It rsyncs the code, reinstalls packages only if
   `requirements*.txt` changed, and restarts the service.

The Pi keeps its own `config.toml`, `secrets/` and `data/`. Display settings changed in the control
panel are saved in `data/settings.json` on each machine separately.

## How it behaves

- **Refreshing**: the app renders the image every time something might have changed, and refreshes
  the panel only when the picture is actually different. In practice that is when events change, at
  midnight, or after you change something. Rapid clicks are batched: the panel starts redrawing
  `debounce_seconds` (1 s) after the last one. Automatic refreshes are at least `min_refresh_seconds`
  apart. Button presses and control-panel changes skip that wait. Events are fetched for 2 months
  back and 6 ahead, so paging through nearby months doesn't wait on Google.
- **Offline**: the last fetched events are cached in `data/events_cache.json`, so a reboot without
  Wi-Fi still shows your calendar. If Google can't be reached for more than 30 minutes, a small red
  note appears at the bottom of the panel.
- **Colors**: the panel has 6 inks (black, white, red, yellow, blue, green). Everything is drawn in
  those exact colors with no anti-aliasing, so text stays crisp and doesn't get dithered. The days
  from the previous and next month get a fine dot pattern that looks light gray.
- **Ghosting**: if the panel ever looks muddy, press **D** or **Redraw panel** in the control panel.
- **Security**: the control panel has no password. Anyone on your Wi-Fi can change what the calendar
  shows, but not read your Google account. Don't expose port 8080 to the internet.

## Troubleshooting

| Symptom | Fix |
| --- | --- |
| `Woah there, some pins we need are in use!` | Add `dtoverlay=spi0-0cs` to `/boot/firmware/config.txt` and reboot. |
| Inky not detected / I2C errors | `sudo raspi-config nonint do_i2c 0 && sudo raspi-config nonint do_spi 0`, then reboot. The app falls back to assuming the 13.3" panel. |
| Panel takes 5-10 s to start redrawing after a button press | The 13.3" driver pauses 0.3 s before each of ~25 commands. `panel_command_delay` under `[display]` shortens it, but it's experimental: 0.02 s stopped the panel refreshing. |
| Log says `Panel didn't respond` | Remove any `panel_command_delay` lines, run `sudo poweroff`, then unplug the power for 10 seconds. |
| Button C does nothing | `button_pins` should be `[5, 6, 25, 24]` for the 13.3". Run `gpioinfo` to check. |
| `Calendar ... not found` | Share the calendar with the service account email ("See all event details"), and check the ID. |
| Sync stopped after a week (OAuth) | The consent screen is still in "Testing". Publish it, then run `wallcal auth` again and copy the new `token.json`. |
| "Key creation is not allowed" in Cloud Console | Your Google Cloud organization blocks service account keys. Use OAuth (Option B). |
| Wrong day highlighted / times off | Set `timezone = "America/New_York"` (etc.) in `config.toml`, or fix it with `sudo raspi-config` → Localisation. |
| Portrait display is upside down | Choose **portrait flipped** in the control panel. |

Fonts in `wallcal/fonts/` are licensed under the SIL Open Font License; see `wallcal/fonts/README.txt`.
