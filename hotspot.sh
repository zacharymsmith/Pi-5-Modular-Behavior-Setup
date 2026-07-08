#!/usr/bin/env bash
#
# PARKED — built-in Wi-Fi hotspot is disabled for now.
#
# Concurrent AP+STA on the Pi 5's single built-in radio proved unreliable
# (the AP is forced onto the STA's channel/band, and 5 GHz AP support in the
# brcmfmac driver is flaky). Access the FlyBox instead via:
#
#   * the lab network:  http://<pi-ip>:8000   (hostname -I to get the IP)
#   * Raspberry Pi Connect (remote, no firewall setup)
#
# If you later want a self-contained hotspot, the reliable path is a cheap USB
# Wi-Fi dongle (its own radio) running hostapd on wlan1 — ask and I'll wire it up.
# The previous concurrent-AP attempt is preserved in git history.

echo "hotspot.sh is parked — see the notes at the top of this file."
echo "Reach the FlyBox at http://$(hostname -I 2>/dev/null | awk '{print $1}'):8000 or via Pi Connect."
