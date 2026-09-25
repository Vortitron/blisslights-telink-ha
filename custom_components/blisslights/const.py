"""Constants for the BlissLights Telink mesh integration."""

DOMAIN = "blisslights"

# Telink "mesh v1" GATT service
TELINK_SERVICE = "00010203-0405-0607-0809-0a0b0c0d1910"
TELINK_SERVICE_SUFFIX = TELINK_SERVICE[-8:]
CHAR_PAIR = f"{TELINK_SERVICE[:-4]}1914"
CHAR_COMMAND = f"{TELINK_SERVICE[:-4]}1912"
CHAR_NOTIFY = f"{TELINK_SERVICE[:-4]}1911"

# Manufacturer data key / vendor id: 529 == 0x0211
VENDOR_ID = 0x0211
MANUFACTURER_KEY = VENDOR_ID  # bleak manufacturer_data dict key

DEFAULT_PASSWORD = "123"
DEFAULT_NAME = "BlissLights"

# Mesh address: this device family answers at 0x0000 and broadcast 0xFFFF.
MESH_ADDRESS = 0x0000

CONF_MESH_NAME = "mesh_name"
CONF_PASSWORD = "password"

# Built-in projector scenes (app resources int-array 0x7f030000) + DIY slots
# (EFFECTDIYS = ids 10..19). Scene 0 = off.
SCENES: dict[int, str] = {
    1: "Stars against nebula",
    2: "Fading",
    3: "Stars",
    4: "Nebula",
    5: "Ocean",
    6: "Space",
    8: "Sunrise",
    9: "RGB auto",
}
DIY_SCENE_ID_START = 10
DIY_SCENE_ID_END = 19  # EFFECTDIY_MAX

# Poll the device over GATT (connect + login + query + disconnect).
UPDATE_INTERVAL_SECONDS = 120
CONNECT_TIMEOUT = 20
RESPONSE_TIMEOUT = 4.0

# Keep the logged-in connection open this long after the last exchange, so a
# burst of commands (and the confirming refresh) share one connect + login.
IDLE_DISCONNECT_SECONDS = 15
# After a command, state is applied optimistically and confirmed by a refresh
# this long after the last command in a burst.
REFRESH_DELAY_SECONDS = 3

# 0x48 / 0x47 channel order.
CHANNELS = ("r", "g", "b", "laser", "motor", "bright", "breathe")
LIGHT_CHANNELS = ("r", "g", "b", "laser")

# The Sky Lite reports brightness as a level (app: Low / Medium / High), not
# the 0-255 the app's slider resources suggest. Values above this are treated
# as raw 0-255 for firmwares that do use the full range.
BRIGHT_LEVELS = 3