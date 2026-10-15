from __future__ import annotations

from itertools import count
from threading import Event, Lock, Thread
from typing import Any

from keyboard import send_key
from mouse import (
    scroll_horizontal,
    scroll_vertical,
    send_button,
)


Action = dict[str, Any]


class MacroEngine:
    def __init__(self):
        self._lock = Lock()
        self._next_run_id = count(1)

        # run_id:
        # (thread, cancel_event, macro_name, trigger_id)
        self._runs: dict[
            int,
            tuple[
                Thread,
                Event,
                str,
                str | None,
            ],
        ] = {}

        # Number of active copies of each macro name.
        self._active_names: dict[str, int] = {}

        # Repeat-mode runs indexed by the physical binding
        # that started them, such as "M1:G7".
        self._trigger_runs: dict[str, int] = {}

        # Lua PlayMacro()/AbortMacro() state.
        #
        # Logitech allows one script-started macro at a time.
        # Replaying the same macro while it is active queues
        # another complete playback. A different macro is
        # blocked until the current script macro finishes.
        self._script_run_id: int | None = None
        self._script_macro_name: str | None = None

    def handle_event(
        self,
        action: Action,
        pressed: bool,
        trigger_id: str | None,
    ) -> str:
        """
        Process a physical DOWN/UP event for a macro.

        Possible results:

            started
            stopped
            blocked
            ignored
        """
        execution_mode = action.get(
            "execution_mode",
            "once",
        )

        if execution_mode == "once":
            if not pressed:
                return "ignored"

            started = self._start(
                action,
                repeat=False,
                trigger_id=None,
            )

            return (
                "started"
                if started
                else "blocked"
            )

        if trigger_id is None:
            raise ValueError(
                "Repeating macros require a trigger ID."
            )

        if execution_mode == "while_held":
            if pressed:
                started = self._start(
                    action,
                    repeat=True,
                    trigger_id=trigger_id,
                )

                return (
                    "started"
                    if started
                    else "blocked"
                )

            stopped = self.stop_trigger(
                trigger_id
            )

            return (
                "stopped"
                if stopped
                else "ignored"
            )

        if execution_mode == "toggle":
            if not pressed:
                return "ignored"

            with self._lock:
                already_running = (
                    trigger_id
                    in self._trigger_runs
                )

            if already_running:
                self.stop_trigger(trigger_id)
                return "stopped"

            started = self._start(
                action,
                repeat=True,
                trigger_id=trigger_id,
            )

            return (
                "started"
                if started
                else "blocked"
            )

        raise ValueError(
            "Unsupported macro execution mode: "
            f"{execution_mode}"
        )

    def _start(
        self,
        action: Action,
        repeat: bool,
        trigger_id: str | None,
    ) -> bool:
        name = str(
            action.get(
                "name",
                "Unnamed Macro",
            )
        )

        allow_overlap = bool(
            action.get(
                "allow_overlap",
                False,
            )
        )

        with self._lock:
            if (
                trigger_id is not None
                and trigger_id
                in self._trigger_runs
            ):
                return False

            active_count = self._active_names.get(
                name,
                0,
            )

            if (
                active_count > 0
                and not allow_overlap
            ):
                return False

            run_id = next(
                self._next_run_id
            )

            cancel_event = Event()

            thread = Thread(
                target=self._run,
                args=(
                    run_id,
                    action,
                    cancel_event,
                    repeat,
                    trigger_id,
                ),
                daemon=True,
                name=f"G13-Macro-{run_id}",
            )

            self._runs[run_id] = (
                thread,
                cancel_event,
                name,
                trigger_id,
            )

            self._active_names[name] = (
                active_count + 1
            )

            if trigger_id is not None:
                self._trigger_runs[
                    trigger_id
                ] = run_id

        try:
            thread.start()
        except Exception:
            self._finish_run(
                run_id,
                name,
                trigger_id,
            )
            raise

        return True

    def play_script_macro(
        self,
        action: Action,
    ) -> str:
        """
        Start a macro for Logitech-compatible PlayMacro().

        Possible results:

            started
            queued
            blocked
        """
        name = str(
            action.get(
                "name",
                "Unnamed Macro",
            )
        )

        allow_overlap = bool(
            action.get(
                "allow_overlap",
                False,
            )
        )

        with self._lock:
            if self._script_run_id is not None:
                return "blocked"

            active_count = self._active_names.get(
                name,
                0,
            )

            if (
                active_count > 0
                and not allow_overlap
            ):
                return "blocked"

            run_id = next(
                self._next_run_id
            )

            cancel_event = Event()

            thread = Thread(
                target=self._run_script_macro,
                args=(
                    run_id,
                    action,
                    cancel_event,
                ),
                daemon=True,
                name=f"G13-Lua-Macro-{run_id}",
            )

            self._runs[run_id] = (
                thread,
                cancel_event,
                name,
                None,
            )

            self._active_names[name] = (
                active_count + 1
            )

            self._script_run_id = run_id
            self._script_macro_name = name

        try:
            thread.start()

        except Exception:
            with self._lock:
                if self._script_run_id == run_id:
                    self._script_run_id = None
                    self._script_macro_name = None

            self._finish_run(
                run_id,
                name,
                None,
            )

            raise

        return "started"

    def abort_script_macro(
        self,
    ) -> bool:
        """
        Cancel the macro started by PlayMacro() and discard
        any queued repeat requests.
        """
        with self._lock:
            run_id = self._script_run_id

            if run_id is None:
                return False

            run = self._runs.get(
                run_id
            )

            if run is None:
                return False

            thread = run[0]
            cancel_event = run[1]

        cancel_event.set()

        cancel_event.set()
        thread.join(timeout=1.0)

        return True

    def _run_script_macro(
        self,
        run_id: int,
        action: Action,
        cancel_event: Event,
    ) -> None:
        name = str(
            action.get(
                "name",
                "Unnamed Macro",
            )
        )

        try:
            self._execute_sequence(
                action,
                cancel_event,
            )

        except Exception as error:
            print(
                f"Lua macro '{name}' failed: "
                f"{error}"
            )

        finally:
            with self._lock:
                if self._script_run_id == run_id:
                    self._script_run_id = None
                    self._script_macro_name = None

            self._finish_run(
                run_id,
                name,
                None,
            )

    def stop_trigger(
        self,
        trigger_id: str,
    ) -> bool:
        with self._lock:
            run_id = self._trigger_runs.get(
                trigger_id
            )

            if run_id is None:
                return False

            run = self._runs.get(run_id)

            if run is None:
                return False

            cancel_event = run[1]

        cancel_event.set()

        return True

    def stop_all(self) -> int:
        """
        Cancel all running macros.

        Returns the number of macros that were active.
        """
        with self._lock:
            runs = list(
                self._runs.values()
            )

        for (
            _,
            cancel_event,
            _,
            _,
        ) in runs:
            cancel_event.set()

        for (
            thread,
            _,
            _,
            _,
        ) in runs:
            thread.join(timeout=1.0)

        return len(runs)

    def _run(
        self,
        run_id: int,
        action: Action,
        cancel_event: Event,
        repeat: bool,
        trigger_id: str | None,
    ) -> None:
        name = str(
            action.get(
                "name",
                "Unnamed Macro",
            )
        )

        repeat_delay_ms = int(
            action.get(
                "repeat_delay_ms",
                25,
            )
        )

        try:
            while not cancel_event.is_set():
                completed = self._execute_sequence(
                    action,
                    cancel_event,
                )

                if not completed:
                    break

                if not repeat:
                    break

                cancelled = cancel_event.wait(
                    repeat_delay_ms / 1000.0
                )

                if cancelled:
                    break

        except Exception as error:
            print(
                f"Macro '{name}' failed: {error}"
            )

        finally:
            self._finish_run(
                run_id,
                name,
                trigger_id,
            )

    def _execute_sequence(
        self,
        action: Action,
        cancel_event: Event,
    ) -> bool:
        """
        Execute one complete macro cycle.

        Any keys or buttons left down by the sequence are
        automatically released at the end of that cycle.
        """
        held_keys: list[int] = []
        held_buttons: list[str] = []

        try:
            steps = action.get(
                "steps",
                [],
            )

            for step in steps:
                if cancel_event.is_set():
                    return False

                should_continue = (
                    self._execute_step(
                        step,
                        cancel_event,
                        held_keys,
                        held_buttons,
                    )
                )

                if not should_continue:
                    return False

            return True

        finally:
            for keycode in reversed(
                held_keys
            ):
                send_key(
                    keycode,
                    False,
                )

            for button in reversed(
                held_buttons
            ):
                send_button(
                    button,
                    False,
                )

    def _execute_step(
        self,
        step: Action,
        cancel_event: Event,
        held_keys: list[int],
        held_buttons: list[str],
    ) -> bool:
        step_type = step["type"]

        if step_type == "delay":
            milliseconds = step[
                "milliseconds"
            ]

            cancelled = cancel_event.wait(
                milliseconds / 1000.0
            )

            return not cancelled

        if step_type == "key_down":
            keycode = step["key"]

            send_key(
                keycode,
                True,
            )

            held_keys.append(keycode)

            return True

        if step_type == "key_up":
            self._release_local_key(
                step["key"],
                held_keys,
            )

            return True

        if step_type == "key_press":
            keycode = step["key"]

            duration = step.get(
                "duration_ms",
                30,
            )

            send_key(
                keycode,
                True,
            )

            held_keys.append(keycode)

            cancelled = cancel_event.wait(
                duration / 1000.0
            )

            if cancelled:
                return False

            self._release_local_key(
                keycode,
                held_keys,
            )

            return True

        if step_type == "mouse_down":
            button = step["button"]

            send_button(
                button,
                True,
            )

            held_buttons.append(button)

            return True

        if step_type == "mouse_up":
            self._release_local_button(
                step["button"],
                held_buttons,
            )

            return True

        if step_type == "mouse_click":
            button = step["button"]

            duration = step.get(
                "duration_ms",
                30,
            )

            send_button(
                button,
                True,
            )

            held_buttons.append(button)

            cancelled = cancel_event.wait(
                duration / 1000.0
            )

            if cancelled:
                return False

            self._release_local_button(
                button,
                held_buttons,
            )

            return True

        if step_type == "wheel":
            direction = step["direction"]
            amount = step["amount"]

            if direction == "vertical":
                scroll_vertical(amount)

            elif direction == "horizontal":
                scroll_horizontal(amount)

            else:
                raise ValueError(
                    "Unsupported macro wheel "
                    f"direction: {direction}"
                )

            return True

        raise ValueError(
            "Unsupported macro step: "
            f"{step_type}"
        )

    @staticmethod
    def _release_local_key(
        keycode: int,
        held_keys: list[int],
    ) -> None:
        for index in range(
            len(held_keys) - 1,
            -1,
            -1,
        ):
            if held_keys[index] != keycode:
                continue

            send_key(
                keycode,
                False,
            )

            held_keys.pop(index)

            return

    @staticmethod
    def _release_local_button(
        button: str,
        held_buttons: list[str],
    ) -> None:
        for index in range(
            len(held_buttons) - 1,
            -1,
            -1,
        ):
            if held_buttons[index] != button:
                continue

            send_button(
                button,
                False,
            )

            held_buttons.pop(index)

            return

    def _finish_run(
        self,
        run_id: int,
        name: str,
        trigger_id: str | None,
    ) -> None:
        with self._lock:
            self._runs.pop(
                run_id,
                None,
            )

            if (
                trigger_id is not None
                and self._trigger_runs.get(
                    trigger_id
                ) == run_id
            ):
                self._trigger_runs.pop(
                    trigger_id,
                    None,
                )

            remaining = (
                self._active_names.get(
                    name,
                    1,
                )
                - 1
            )

            if remaining <= 0:
                self._active_names.pop(
                    name,
                    None,
                )
            else:
                self._active_names[name] = (
                    remaining
                )


_engine = MacroEngine()


def handle_macro_event(
    action: Action,
    pressed: bool,
    trigger_id: str | None,
) -> str:
    return _engine.handle_event(
        action,
        pressed,
        trigger_id,
    )


def play_script_macro(
    action: Action,
) -> str:
    return _engine.play_script_macro(
        action
    )


def abort_script_macro() -> bool:
    return _engine.abort_script_macro()


def stop_all_macros() -> int:
    return _engine.stop_all()
