#!/usr/bin/env bash
# Nexus headless desktop session on Xvfb :99 — run by nexus-desktop.service.
# Idempotent: every component is started only if it is not already alive on
# this display. Keeps running (supervises Chrome) so systemd can Restart=always.
set -u
export DISPLAY="${DISPLAY:-:99}"
unset WAYLAND_DISPLAY XDG_SESSION_TYPE
ROOT="$HOME/AI_Agent"
DESK="$ROOT/desktop"
RUN="$DESK/run"
PROFILE="$DESK/chrome-profile"
OB="$DESK/openbox"
START_URL="${NEXUS_DESKTOP_START_URL:-about:blank}"
mkdir -p "$RUN" "$PROFILE" "$OB"

log() { echo "[desktop_session $(date +%H:%M:%S)] $*"; }

# ── wait for X ─────────────────────────────────────────────────────────
for _ in $(seq 1 30); do
  xdpyinfo >/dev/null 2>&1 && break
  sleep 1
done
xdpyinfo >/dev/null 2>&1 || { log "no X server on $DISPLAY"; exit 1; }

# ── openbox config (minimal: titles + focus-new-windows, no menu clutter) ──
if [ ! -f "$OB/rc.xml" ]; then
cat > "$OB/rc.xml" <<'XML'
<?xml version="1.0" encoding="UTF-8"?>
<openbox_config xmlns="http://openbox.org/3.4/rc">
  <focus>
    <focusNew>yes</focusNew>
    <followMouse>no</followMouse>
    <raiseOnFocus>yes</raiseOnFocus>
  </focus>
  <placement><policy>Smart</policy><center>no</center></placement>
  <theme>
    <name>Clearlooks</name>
    <titleLayout>NLIMC</titleLayout>
    <keepBorder>yes</keepBorder>
    <font place="ActiveWindow"><name>DejaVu Sans</name><size>9</size></font>
    <font place="InactiveWindow"><name>DejaVu Sans</name><size>9</size></font>
  </theme>
  <desktops><number>1</number><firstdesk>1</firstdesk></desktops>
  <keyboard>
    <keybind key="A-Tab"><action name="NextWindow"><finalactions><action name="Focus"/><action name="Raise"/></finalactions></action></keybind>
  </keyboard>
  <mouse>
    <context name="Frame">
      <mousebind button="A-Left" action="Press"><action name="Focus"/><action name="Raise"/></mousebind>
    </context>
    <context name="Titlebar">
      <mousebind button="Left" action="Press"><action name="Focus"/><action name="Raise"/></mousebind>
      <mousebind button="Left" action="Drag"><action name="Move"/></mousebind>
    </context>
    <context name="Client">
      <mousebind button="Left" action="Press"><action name="Focus"/><action name="Raise"/></mousebind>
      <mousebind button="Middle" action="Press"><action name="Focus"/><action name="Raise"/></mousebind>
      <mousebind button="Right" action="Press"><action name="Focus"/><action name="Raise"/></mousebind>
    </context>
  </mouse>
  <menu><file>menu.xml</file></menu>
  <applications>
    <application class="*"><decor>yes</decor><focus>yes</focus></application>
  </applications>
</openbox_config>
XML
fi
if [ ! -f "$OB/menu.xml" ]; then
cat > "$OB/menu.xml" <<'XML'
<?xml version="1.0" encoding="UTF-8"?>
<openbox_menu xmlns="http://openbox.org/3.4/menu">
  <menu id="root-menu" label="Nexus">
    <item label="Chrome"><action name="Execute"><execute>google-chrome</execute></action></item>
    <item label="xterm"><action name="Execute"><execute>xterm</execute></action></item>
  </menu>
</openbox_menu>
XML
fi

# alive <pidfile> -> 0 if the recorded pid is still running
alive() { [ -f "$1" ] && kill -0 "$(cat "$1")" 2>/dev/null; }

# ── background ─────────────────────────────────────────────────────────
xsetroot -solid '#1b1f2a' 2>/dev/null || true

# ── AT-SPI bus for this display (gives Chrome's a11y tree to pyatspi) ──
if ! xprop -root AT_SPI_BUS 2>/dev/null | grep -q '='; then
  if [ -x /usr/libexec/at-spi-bus-launcher ]; then
    /usr/libexec/at-spi-bus-launcher --launch-immediately >/dev/null 2>&1 &
    echo $! > "$RUN/at-spi.pid"
    log "started at-spi-bus-launcher"
    sleep 1
  fi
fi

# ── window manager ────────────────────────────────────────────────────
if ! alive "$RUN/openbox.pid"; then
  if command -v openbox >/dev/null; then
    openbox --config-file "$OB/rc.xml" >/dev/null 2>&1 &
    echo $! > "$RUN/openbox.pid"
    log "started openbox"
    sleep 1
  else
    log "openbox not installed (run scripts/sudo_phase3.sh) — no window manager"
  fi
fi

# ── panel ─────────────────────────────────────────────────────────────
if ! alive "$RUN/tint2.pid"; then
  if command -v tint2 >/dev/null; then
    tint2 >/dev/null 2>&1 &
    echo $! > "$RUN/tint2.pid"
    log "started tint2"
  fi
fi

# ── Chrome (persistent profile, renderer a11y on) — supervised ────────
chrome_window_up() {
  xdotool search --onlyvisible --class "google-chrome" 2>/dev/null | grep -q .
}
launch_chrome() {
  google-chrome \
    --user-data-dir="$PROFILE" \
    --ozone-platform=x11 \
    --remote-debugging-port=9222 \
    --window-size=1920,1040 --window-position=0,0 \
    --no-first-run --no-default-browser-check \
    --disable-gpu-sandbox --force-renderer-accessibility \
    --password-store=basic --disable-features=TranslateUI \
    --disable-session-crashed-bubble --hide-crash-restore-bubble \
    "$START_URL" >/dev/null 2>&1 &
  echo $! > "$RUN/chrome.pid"
  log "started google-chrome (profile $PROFILE)"
}

if ! chrome_window_up && ! alive "$RUN/chrome.pid"; then
  launch_chrome
fi

log "session up on $DISPLAY — supervising"
while true; do
  sleep 10
  if ! chrome_window_up && ! alive "$RUN/chrome.pid"; then
    log "chrome gone — relaunching"
    launch_chrome
    sleep 5
  fi
  if [ -f "$RUN/openbox.pid" ] && ! alive "$RUN/openbox.pid" && command -v openbox >/dev/null; then
    openbox --config-file "$OB/rc.xml" >/dev/null 2>&1 &
    echo $! > "$RUN/openbox.pid"
    log "restarted openbox"
  fi
done
