"""Environmental watchdog — Pimoroni Multi-Sensor Stick (BME280 + LTR-559 + LSM6DS3).

Shares the SAME I2C bus as the PCA9685 (SDA=GPIO2/pin3, SCL=GPIO3/pin5); the stick's
addresses (0x76 BME280, 0x23 LTR-559, 0x6a LSM6DS3) don't clash with the PCA9685 (0x40),
so it needs NO extra GPIO. Mock-aware: if the libraries/hardware are absent it reports
hw=False and simply returns nothing, so the app runs anywhere.

Purpose: flag INCIDENTS that could confound behaviour data —
  * light spikes/drops  (room light, unexpected flash)     -> tracking / opto confound
  * temp / humidity out of band or moving fast             -> flies are climate-sensitive
  * physical bumps / vibration (accel jolt on the IMU)     -> motion artifacts, jostled rig
so they land on the same timeline as feeding/opto events and you can spot cause-and-effect.

Pi libraries (install once, into SYSTEM python3):
    pip3 install --break-system-packages pimoroni-bme280 ltr559 lsm6ds3 smbus2
NOTE: it's **pimoroni-bme280** (provides the BME280 class), NOT the generic 'bme280' package.
"""
from __future__ import annotations
import time
import threading
from collections import deque

from config import (SENSOR_I2C_BUS, ENVIRO_POLL_S, TEMP_BAND_C, HUM_BAND_PCT,
                    LUX_JUMP_FRAC, BUMP_G)


class EnviroStick:
    def __init__(self):
        self.hw = False
        self.message = "environmental sensor: not present (mock)"
        self._bme = self._ltr = self._imu = None
        self.latest: dict = {}
        self._prev: dict = {}
        self._recent = deque(maxlen=8)      # recent incidents for the UI banner
        self._lock = threading.Lock()
        try:
            from smbus2 import SMBus
            bus = SMBus(SENSOR_I2C_BUS)
            from bme280 import BME280
            self._bme = BME280(i2c_dev=bus)              # 0x76
            try:
                from ltr559 import LTR559
                self._ltr = LTR559(i2c_dev=bus)          # 0x23 (light + proximity)
            except Exception:
                self._ltr = None
            try:
                from lsm6ds3 import LSM6DS3
                self._imu = LSM6DS3(i2c_dev=bus)         # 0x6a (accel + gyro)
            except Exception:
                self._imu = None
            self._bme.get_temperature()                  # probe: raises if not really wired
            self.hw = True
            have = ["BME280"] + (["LTR559"] if self._ltr else []) + (["LSM6DS3"] if self._imu else [])
            self.message = "multi-sensor stick online (" + "+".join(have) + ")"
        except Exception as e:
            self.hw = False
            self.message = f"environmental sensor: mock ({e.__class__.__name__})"

    # ---- reading ----
    def read(self) -> dict:
        if not self.hw:
            return {}
        r: dict = {}
        try:
            r["temp_C"] = round(float(self._bme.get_temperature()), 2)
            r["pressure_hPa"] = round(float(self._bme.get_pressure()), 1)
            r["humidity_pct"] = round(float(self._bme.get_humidity()), 1)
        except Exception:
            pass
        if self._ltr is not None:
            try:
                self._ltr.update_sensor()
                r["lux"] = round(float(self._ltr.get_lux()), 1)
                r["prox"] = int(self._ltr.get_proximity())
            except Exception:
                pass
        if self._imu is not None:
            try:
                ax, ay, az = self._imu.get_accelerometer()          # units of g
                r["accel_g"] = round((ax * ax + ay * ay + az * az) ** 0.5, 3)
            except Exception:
                pass
        with self._lock:
            self.latest = dict(r, t=time.time())
        return r

    # ---- incident detection (compare to previous reading + fixed bands) ----
    def incidents(self, r: dict) -> list:
        out = []
        p = self._prev
        if "lux" in r and "lux" in p:                    # sudden fractional light change
            base = max(1.0, p["lux"])
            if abs(r["lux"] - p["lux"]) / base > LUX_JUMP_FRAC:
                out.append(("light", f"lux {p['lux']:.0f} -> {r['lux']:.0f}"))
        if "temp_C" in r and not (TEMP_BAND_C[0] <= r["temp_C"] <= TEMP_BAND_C[1]):
            out.append(("temp", f"{r['temp_C']:.1f} C outside {TEMP_BAND_C[0]}-{TEMP_BAND_C[1]}"))
        if "humidity_pct" in r and not (HUM_BAND_PCT[0] <= r["humidity_pct"] <= HUM_BAND_PCT[1]):
            out.append(("humidity", f"{r['humidity_pct']:.0f}% outside {HUM_BAND_PCT[0]}-{HUM_BAND_PCT[1]}"))
        if "accel_g" in r and abs(r["accel_g"] - 1.0) > BUMP_G:      # 1 g = at rest
            out.append(("bump", f"accel {r['accel_g']:.2f} g (rig disturbed)"))
        if r:
            self._prev = dict(r)
        for kind, detail in out:
            self._recent.appendleft({"t": time.time(), "kind": kind, "detail": detail})
        return out

    def status(self) -> dict:
        with self._lock:
            return {"hw": self.hw, "message": self.message, "latest": dict(self.latest),
                    "recent": list(self._recent)}


sensors = EnviroStick()
