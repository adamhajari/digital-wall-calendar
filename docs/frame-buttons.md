# Optional hardware: frame buttons

Once the panel is in a frame, the matboard hides the Inky's onboard buttons. This guide adds two buttons
on the side of the frame instead, with no soldering:

| Button | Press | Hold |
| --- | --- | --- |
| **Left** | Previous month | Back to today |
| **Right** | Next month | Sync + redraw |
| **Both together** | — | Hold 3 s: shut the Pi down |

The onboard buttons keep working. Both frame buttons plug into the 10-pin expansion header on the back of
the Inky Impression 13.3", not the Pi.

Turn them on in `config.toml` on the Pi, under `[display]`:

```toml
buttons = true
frame_button_pins = [18, 4]   # [left, right]
hold_seconds = 1.0            # how long a hold is
power_off_hold_seconds = 3    # hold both this long to shut down; 0 = off
```

A hold fires as soon as the button has been down for `hold_seconds`, so you don't have to let go to see
it work. A short press fires when you let go.

### Shutting down

Hold both buttons for 3 seconds to shut the Pi down cleanly, so it's safe to unplug. Holding both cancels
their single actions, so nothing else happens. If the panel is mid-refresh, the Pi waits for it to
finish first, since cutting power mid-refresh can leave the panel stuck until it's unplugged.

- The panel keeps showing the last calendar after the Pi is off, because e-ink needs no power to hold an
  image. Unplug the Pi once its green activity light has stopped blinking.
- To turn it back on, unplug the power and plug it back in.
- The shutdown uses `sudo`, which works without a password for the first user Raspberry Pi OS creates.
  If you run wallcal as a different user, the log will show `Power off failed`.

---

## Parts

Already on hand: the Pi Zero 2 W and the Inky Impression 13.3". The parts below are what we used.
Similar parts work too.

| Part | Example | Qty | Used for |
| --- | --- | --- | --- |
| 8 mm momentary push button, normally open, pre-wired | DMWD, black (2-pack) | 2 | Frame buttons. 8 mm mounting hole, 10.5 mm head, 20 cm leads. |
| Male-to-female dupont jumper wires | any | 4 | Connect the buttons to the header. |
| Inline lever wire connectors ("2 in 2 out") | ALITOVE, 10 pcs | 2 | Join button leads to jumpers (two joins each). |

**Choosing buttons:**
- Get **momentary**, not latching.
- Pre-wired leads avoid soldering.

---

## Inky expansion header

Every wire goes into the black 10-pin female socket near the bottom-right of the Inky's back, below the
two Qw/ST connectors. Labels are printed beside it. Read top to bottom:

| # | Label | Pi GPIO | Connects to |
| --- | --- | --- | --- |
| 1 | 3V3 | — | Nothing |
| 2 | SDA | GPIO 2 | Nothing. The panel uses it to identify itself. |
| 3 | SCL | GPIO 3 | Nothing, same reason |
| 4 | GP4 | GPIO 4 | **Right** button |
| 5 | GND | — | **Right** button |
| 6 | TX | GPIO 14 | Nothing |
| 7 | RX | GPIO 15 | Nothing |
| 8 | GP18 | GPIO 18 | **Left** button |
| 9 | GND | — | **Left** button |
| 10 | 5V | — | Nothing |

Each button uses a GPIO pin and the GND pin directly below it, so no shared ground wire is needed. TX and
RX stay free, so you don't need to change the Pi's serial settings.

The onboard buttons A–D (GPIO 5, 6, 25, 24) and the panel's own pins aren't on this header and aren't
touched. The pin numbers above come from Pimoroni's
[schematic for the 13.3" (2025 Edition)](https://cdn.shopify.com/s/files/1/0174/1800/files/inky_impression_133_spectra_schematic.pdf).

---

## Wiring

Each button connects its GPIO pin to ground while it's pressed. The Pi's built-in pull-up holds the pin
high the rest of the time, so no resistors are needed. Buttons without an LED have no polarity, so
either lead can go to either side.

```
Inky header                                        Frame buttons
-----------                                        -------------
 4  GP4   -- jumper -- [lever connector] -- lead --+
                                                   Right button
 5  GND   -- jumper -- [lever connector] -- lead --+

 8  GP18  -- jumper -- [lever connector] -- lead --+
                                                   Left button
 9  GND   -- jumper -- [lever connector] -- lead --+
```

1. **Unplug the Pi.**
2. **Prepare the jumpers.** Cut the female end off four male-to-female jumpers and strip 10 mm of
   insulation from each cut end.
3. **Join each lead to a jumper** with the inline lever connectors (strip each button lead to 10 mm, lift
   the lever, insert, close). Each connector holds two joins, so use one connector per button.
4. **Plug the jumpers into the header:**
   - **Left** button: one jumper into **GP18** (pin 8), the other into **GND** (pin 9)
   - **Right** button: one jumper into **GP4** (pin 4), the other into **GND** (pin 5)
5. **Label the jumpers.** Tape a small flag on each one, so the Inky can be unplugged and everything
   plugged back in correctly.

### Mounting

1. **Placement:** put the buttons on the frame side closest to the expansion header. The 20 cm leads,
   plus a jumper, need to reach it. Put **left** on the left (or top) so it matches "previous".
2. **Holes:** drill one **8 mm** hole per button. Space them at least 20 mm apart so they're easy to tell
   apart by feel.
3. **Depth:** the body extends about 17 mm into the frame (12 mm thread + 5 mm cable boot). Allow another
   ~10 mm for the leads to bend, so 25–30 mm of clearance inward from the frame wall.
4. **Fixing:** the threaded body is 7.8 mm across, so it fits a ~10 mm gap behind a matboard.
   - The nut is probably wider than 10 mm. If it won't fit, drill the hole so the nut lands beside the
     matboard edge, or skip the nut and glue the button into a snug hole with epoxy.
   - If the frame wall is thicker than about 10 mm, the nut can't reach the thread. Use epoxy.

---

## Testing

Check each button with the Pi's own tools before relying on the software. For each pin (18 and 4), turn on
its pull-up, then read it while holding the button down and again after letting go:

```
pinctrl set 4 ip pu
pinctrl get 4
```

It should read `hi` when released and `lo` when pressed. Repeat with 18.

Then restart wallcal (`sudo systemctl restart wallcal`) and watch the log with `journalctl -u wallcal -f`.
Each press logs `Button left pressed` or `Button right held`, and so on.

If the left button does nothing and the log shows `Could not set up buttons`, check that
`/boot/firmware/config.txt` has no `dtoverlay=pwm` line using GPIO 18. A PWM overlay on that pin keeps it
busy.

## Don'ts

- Don't wire with the Pi powered.
- Don't connect anything to **SDA** or **SCL**.
- Don't connect a button to **3V3** or **5V**. A press would short the supply.
