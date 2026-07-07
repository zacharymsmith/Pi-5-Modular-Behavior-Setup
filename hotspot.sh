#!/usr/bin/env bash
#
# Concurrent Wi-Fi hotspot (AP) while the Pi stays connected to your normal
# Wi-Fi (STA). Creates a virtual "ap0" interface on the Pi 5's built-in radio,
# so the FlyBox is reachable BOTH:
#   * directly over its own Wi-Fi  -> http://192.168.60.1:8000
#   * and via your existing network / Pi Connect (internet stays up)
#
#   sudo bash hotspot.sh start [SSID] [PASSWORD]
#   sudo bash hotspot.sh stop
#   sudo bash hotspot.sh status
#
# IMPORTANT
#  - Run this from the Pi's own screen/keyboard or an ETHERNET ssh session, NOT
#    over the Wi-Fi you're reconfiguring.
#  - One radio means AP and STA must share a channel, so the hotspot adopts
#    whatever channel your Wi-Fi is on. If your Wi-Fi roams channels the AP can
#    drop. For rock-solid concurrent use, add a cheap USB Wi-Fi dongle and point
#    hostapd at that interface instead (wlan1).

set -uo pipefail
CMD="${1:-start}"
SSID="${2:-FlyBox}"
PASS="${3:-flybox1234}"
AP_IF="ap0"
AP_IP="192.168.60.1"
UP_IF="wlan0"

need_root() { [ "${EUID:-$(id -u)}" -eq 0 ] || { echo "Run with sudo."; exit 1; }; }

start() {
  need_root
  if [ ${#PASS} -lt 8 ]; then echo "Password must be >= 8 chars."; exit 1; fi
  echo "Installing hostapd + dnsmasq…"
  apt-get install -y -qq hostapd dnsmasq iw >/dev/null 2>&1

  # channel of the current Wi-Fi link (AP must match on a single radio)
  local freq ch
  freq=$(iw dev "$UP_IF" link 2>/dev/null | awk '/freq/{print $2; exit}')
  if [ -n "${freq:-}" ]; then ch=$(( (freq - 2407) / 5 )); else ch=6; fi
  [ "$ch" -lt 1 ] 2>/dev/null || [ "$ch" -gt 14 ] 2>/dev/null && ch=6

  echo "Creating $AP_IF (channel $ch)…"
  iw dev "$AP_IF" del 2>/dev/null
  iw dev "$UP_IF" interface add "$AP_IF" type __ap 2>/dev/null \
    || iw phy phy0 interface add "$AP_IF" type __ap
  nmcli device set "$AP_IF" managed no 2>/dev/null   # keep NetworkManager off it
  ip link set "$AP_IF" up
  ip addr flush dev "$AP_IF"
  ip addr add "${AP_IP}/24" dev "$AP_IF"

  cat >/tmp/flybox_hostapd.conf <<EOF
interface=$AP_IF
driver=nl80211
ssid=$SSID
hw_mode=g
channel=$ch
wmm_enabled=1
auth_algs=1
wpa=2
wpa_key_mgmt=WPA-PSK
rsn_pairwise=CCMP
wpa_passphrase=$PASS
EOF

  cat >/tmp/flybox_dnsmasq.conf <<EOF
interface=$AP_IF
bind-interfaces
dhcp-range=192.168.60.10,192.168.60.100,255.255.255.0,24h
EOF

  # let hotspot clients reach the internet through the STA link
  sysctl -w net.ipv4.ip_forward=1 >/dev/null
  iptables -t nat -C POSTROUTING -o "$UP_IF" -j MASQUERADE 2>/dev/null \
    || iptables -t nat -A POSTROUTING -o "$UP_IF" -j MASQUERADE

  pkill -f flybox_dnsmasq 2>/dev/null
  pkill -f flybox_hostapd 2>/dev/null
  sleep 1
  dnsmasq -C /tmp/flybox_dnsmasq.conf -x /tmp/flybox_dnsmasq.pid
  hostapd -B /tmp/flybox_hostapd.conf

  echo
  echo "Hotspot '$SSID' is up on channel $ch."
  echo "  Connect a laptop/phone to '$SSID' (password: $PASS)"
  echo "  Then open  http://$AP_IP:8000"
  echo "  Your Pi stays online on $UP_IF for Pi Connect / internet."
}

stop() {
  need_root
  pkill -f flybox_hostapd 2>/dev/null
  pkill -f flybox_dnsmasq 2>/dev/null
  iptables -t nat -D POSTROUTING -o "$UP_IF" -j MASQUERADE 2>/dev/null
  iw dev "$AP_IF" del 2>/dev/null
  echo "Hotspot stopped."
}

status() {
  echo "== interfaces =="; iw dev 2>/dev/null | grep -E 'Interface|type|channel' || true
  echo "== hostapd =="; pgrep -a -f flybox_hostapd || echo "not running"
  echo "== dnsmasq =="; pgrep -a -f flybox_dnsmasq || echo "not running"
}

case "$CMD" in
  start) start ;;
  stop)  stop ;;
  status) status ;;
  *) echo "usage: sudo bash hotspot.sh {start|stop|status} [SSID] [PASSWORD]" ;;
esac
