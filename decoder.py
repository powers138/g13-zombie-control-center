import math

START_DEADZONE = 30
STOP_DEADZONE = 15
NUM_G_KEYS = 22

# ------------------------
# LCD Buttons
# ------------------------
LCD1_MASK = 0x02
LCD2_MASK = 0x04
LCD3_MASK = 0x08
LCD4_MASK = 0x10

# ------------------------
# M buttons
# ------------------------

M1_MASK = 0x20
M2_MASK = 0x40
M3_MASK = 0x80


# ------------------------
# Extra buttons (byte 7)
# ------------------------

MR_MASK  = 0x01
G23_MASK = 0x02
G24_MASK = 0x04
G25_MASK = 0x08

LCD_BUTTONS = (
    (LCD1_MASK, "LCD1"),
    (LCD2_MASK, "LCD2"),
    (LCD3_MASK, "LCD3"),
    (LCD4_MASK, "LCD4"),
)

M_BUTTONS = (
    (M1_MASK, "M1"),
    (M2_MASK, "M2"),
    (M3_MASK, "M3"),
)

EXTRA_BUTTONS = (
    (MR_MASK, "MR"),
    (G23_MASK, "G23"),
    (G24_MASK, "G24"),
    (G25_MASK, "G25"),
)

class G13Decoder:

    def __init__(
        self,
        handle_button,
        joystick,
        joystick_settings,
    ):

        self.handle_button = handle_button
        self.joystick = joystick
        self.g_buttons = [f"G{i}" for i in range(1, NUM_G_KEYS + 1)]

# ------------------------
# Joystick tuning
# ------------------------

        self.center_x = 151.0
        self.center_y = 125.0

        self.joystick_buttons = {
            "G26": False,  # Up
            "G27": False,  # Right
            "G28": False,  # Down
            "G29": False,  # Left
        }

        self.joystick_mode = "bindings"

        self.update_joystick_settings(
            joystick_settings
        )

        self.active_x = False
        self.active_y = False

# ------------------------

        self.prev_g = 0
        self.prev_m = (0, 0)

        self.prev_extra = (0, 0)

    def update_joystick_settings(
        self,
        joystick_settings,
    ) -> None:
        if not isinstance(
            joystick_settings,
            dict,
        ):
            raise ValueError(
                "Joystick settings must be an object."
            )

        mode = joystick_settings.get(
            "mode",
            "bindings",
        )

        if mode not in {"bindings", "mouse"}:
            raise ValueError(
                "Joystick mode must be "
                "'bindings' or 'mouse'."
            )

        previous_mode = getattr(
            self,
            "joystick_mode",
            None,
        )

        # Release digital directions when leaving
        # key-binding mode.
        if (
            previous_mode == "bindings"
            and mode != "bindings"
        ):
            for button, pressed in (
                self.joystick_buttons.items()
            ):
                if pressed:
                    self.handle_button(
                        button,
                        False,
                    )

                self.joystick_buttons[button] = False

        # Stop cursor movement when leaving mouse mode.
        if (
            previous_mode == "mouse"
            and mode != "mouse"
        ):
            self.joystick.set_position(0, 0)

        self.joystick_mode = mode

        self.CENTER_ALPHA = float(
            joystick_settings["center_alpha"]
        )

        self.START_DEADZONE = float(
            joystick_settings["start_deadzone"]
        )

        self.STOP_DEADZONE = float(
            joystick_settings["stop_deadzone"]
        )

        self.LEFT_GAIN = float(
            joystick_settings["left_gain"]
        )

        self.RIGHT_GAIN = float(
            joystick_settings["right_gain"]
        )

        self.UP_GAIN = float(
            joystick_settings["up_gain"]
        )

        self.DOWN_GAIN = float(
            joystick_settings["down_gain"]
        )

        # Reset threshold state when switching modes.
        if previous_mode != mode:
            self.active_x = False
            self.active_y = False

    def process_joystick(self, data):

        x = data[1]
        y = data[2]

#
# Slowly learn center while stick is idle
#
        if abs(x - self.center_x) < 15 and abs(y - self.center_y) < 15:
            self.center_x += (x - self.center_x) * self.CENTER_ALPHA
            self.center_y += (y - self.center_y) * self.CENTER_ALPHA

#
# Calculate offsets
#
        dx = x - self.center_x
        dy = y - self.center_y

#
# Per-direction normalization
#
        if dx >= 0:
            nx = dx / (255 - self.center_x)
        else:
            nx = dx / self.center_x

        if dy >= 0:
            ny = dy / (255 - self.center_y)
        else:
            ny = dy / self.center_y

#
# Clamp
#
        nx = max(-1.0, min(1.0, nx))
        ny = max(-1.0, min(1.0, ny))


# Clamp again in case gains exceed 1.0
        nx = max(-1.0, min(1.0, nx))
        ny = max(-1.0, min(1.0, ny))

#
# Hysteresis
#
        magx = abs(nx)

        if self.active_x:
            if magx < self.STOP_DEADZONE:
                self.active_x = False
        else:
            if magx > self.START_DEADZONE:
                self.active_x = True

        if not self.active_x:
            nx = 0.0

        magy = abs(ny)

        if self.active_y:
            if magy < self.STOP_DEADZONE:
                self.active_y = False
        else:
            if magy > self.START_DEADZONE:
                self.active_y = True

        if not self.active_y:
            ny = 0.0

# -------------------------
# Digital joystick bindings
# -------------------------

        if self.joystick_mode == "bindings":

            # Ensure mouse movement is stopped.
            self.joystick.set_position(0, 0)

            direction_states = {
                "G26": ny < 0.0,  # Up
                "G27": nx > 0.0,  # Right
                "G28": ny > 0.0,  # Down
                "G29": nx < 0.0,  # Left
            }

            for button, pressed in direction_states.items():

                if pressed != self.joystick_buttons[button]:
                    self.handle_button(button, pressed)
                    self.joystick_buttons[button] = pressed

            return

#
# Circular normalization
#
        length = math.hypot(nx, ny)

        if length > 1.0:
            nx /= length
            ny /= length

#
# Gentle response curve
#
        nx = math.copysign(abs(nx) ** 1.5, nx)
        ny = math.copysign(abs(ny) ** 1.5, ny)

#
# Scale to virtual joystick
#
        if nx >= 0:
            move_x = int(round(nx * 12 * self.RIGHT_GAIN))
        else:
            move_x = int(round(nx * 12 * self.LEFT_GAIN))
        if ny >= 0:
            move_y = int(round(ny * 12 * self.DOWN_GAIN))
        else:
            move_y = int(round(ny * 12 * self.UP_GAIN))

        if (move_x, move_y) != self.prev_m:
            self.joystick.set_position(move_x, move_y)
            self.prev_m = (move_x, move_y)

    def decode(self, data):
        self.process_joystick(data)
        self.process_buttons(data)
        self.process_extra_buttons(data)

# -------------------------
# Process Buttons
# -------------------------

    def process_buttons(self, data):

        keys1 = data[3]
        keys2 = data[4]
        keys3 = data[5]

        combined = (
            (keys3 << 16) |
            (keys2 << 8) |
            keys1
        )

        if combined != self.prev_g:

            changed = combined ^ self.prev_g

            for bit, name in enumerate(self.g_buttons):

                mask = 1 << bit

                if changed & mask:
                    self.handle_button(
                        name,
                        bool(combined & mask)
                    )

            self.prev_g = combined

    def _process_bit(self, old, new, mask, name):

        if (new & mask) and not (old & mask):
            self.handle_button(name, True)

        if not (new & mask) and (old & mask):
            self.handle_button(name, False)

    def process_extra_buttons(self, data):

        old6, old7 = self.prev_extra

        new6 = data[6]
        new7 = data[7]

        for mask, name in M_BUTTONS:
            self._process_bit(old6, new6, mask, name)

        for mask, name in LCD_BUTTONS:
            self._process_bit(old6, new6, mask, name)

        for mask, name in EXTRA_BUTTONS:
            self._process_bit(old7, new7, mask, name)

        self.prev_extra = (new6, new7)

