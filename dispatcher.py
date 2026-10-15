import threading
import time

from keyboard import send_chord
from macro_engine import handle_macro_event
from mouse import (
    scroll_horizontal,
    scroll_vertical,
    send_button,
)

WHEEL_REPEAT_INITIAL_DELAY = 0.0
WHEEL_REPEAT_INTERVAL = 0.05

_wheel_repeat_lock = threading.Lock()
_wheel_repeat_stops = {}

def execute_action(
    action,
    pressed,
    source_id=None,
):
    action_type = action["type"]

    if action_type == "keyboard":
        execute_keyboard(
            action,
            pressed,
        )
        return

    if action_type == "mouse":
        execute_mouse(
            action,
            pressed,
        )
        return

    if action_type == "wheel":
        execute_wheel(
            action,
            pressed,
            source_id,
        )
        return

    if action_type == "macro":
        execute_macro(
            action,
            pressed,
            source_id,
        )
        return

    print(
        f"Unknown action type: {action_type}"
    )


def execute_keyboard(action, pressed):
    print(
        f"{action['name']} "
        f"{'DOWN' if pressed else 'UP'}"
    )

    send_chord(
        action["key"],
        action.get(
            "modifiers",
            [],
        ),
        pressed,
    )

def _run_double_click(
    button: str,
) -> None:
    for click_index in range(2):
        send_button(
            button,
            True,
        )

        time.sleep(0.03)

        send_button(
            button,
            False,
        )

        if click_index == 0:
            time.sleep(0.05)

def execute_mouse(action, pressed):
    click_count = action.get(
        "click_count",
    )

    if click_count == 2:
        if not pressed:
            return

        threading.Thread(
            target=_run_double_click,
            args=(
                action["button"],
            ),
            daemon=True,
        ).start()

        print(
            f"{action['name']} DOUBLE CLICK"
        )

        return

    print(
        f"{action['name']} "
        f"{'DOWN' if pressed else 'UP'}"
    )

    send_button(
        action["button"],
        pressed,
    )


def _send_wheel(action):
    direction = action["direction"]
    amount = action["amount"]

    if direction == "vertical":
        scroll_vertical(amount)

    elif direction == "horizontal":
        scroll_horizontal(amount)

    else:
        raise ValueError(
            "Unsupported wheel direction: "
            f"{direction}"
        )


def _run_wheel_repeat(
    action,
    stop_event,
):
    if stop_event.wait(
        WHEEL_REPEAT_INITIAL_DELAY
    ):
        return

    while not stop_event.is_set():
        _send_wheel(
            action
        )

        if stop_event.wait(
            WHEEL_REPEAT_INTERVAL
        ):
            return


def execute_wheel(
    action,
    pressed,
    source_id=None,
):
    if source_id is None:
        if pressed:
            _send_wheel(
                action
            )

        return

    if pressed:
        with _wheel_repeat_lock:
            previous_stop = (
                _wheel_repeat_stops.pop(
                    source_id,
                    None,
                )
            )

            if previous_stop is not None:
                previous_stop.set()

            stop_event = threading.Event()

            _wheel_repeat_stops[
                source_id
            ] = stop_event

        _send_wheel(
            action
        )

        threading.Thread(
            target=_run_wheel_repeat,
            args=(
                dict(action),
                stop_event,
            ),
            daemon=True,
        ).start()

        print(
            f"{action['name']} "
            f"({action['amount']}) STARTED"
        )

        return

    with _wheel_repeat_lock:
        stop_event = (
            _wheel_repeat_stops.pop(
                source_id,
                None,
            )
        )

    if stop_event is not None:
        stop_event.set()

        print(
            f"{action['name']} STOPPED"
        )


def execute_macro(
    action,
    pressed,
    source_id,
):
    result = handle_macro_event(
        action,
        pressed,
        source_id,
    )

    if result == "ignored":
        return

    if result == "started":
        print(
            f"{action['name']} STARTED"
        )
        return

    if result == "stopped":
        print(
            f"{action['name']} STOPPED"
        )
        return

    if result == "blocked":
        print(
            f"{action['name']} "
            "is already running."
        )
