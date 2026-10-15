import fcntl
import threading
import time

from mouse import move_relative
from profiles_to_go import PROFILES_TO_GO_LOCK_PATH


class JoystickController:

    def __init__(self):

        self.dx = 0
        self.dy = 0

        self.running = True

        self.thread = threading.Thread(
            target=self.run,
            daemon=True
        )

        self.thread.start()

    def set_position(self, dx, dy):

        if dx == self.dx and dy == self.dy:
            return

        self.dx = dx
        self.dy = dy

    def run(self):

        while self.running:

            if self.dx != 0 or self.dy != 0:
                try:
                    with PROFILES_TO_GO_LOCK_PATH.open(
                        "a+b"
                    ) as lock_file:
                        fcntl.flock(
                            lock_file.fileno(),
                            (
                                fcntl.LOCK_SH
                                | fcntl.LOCK_NB
                            ),
                        )

                        try:
                            move_relative(
                                self.dx,
                                self.dy,
                            )

                        finally:
                            fcntl.flock(
                                lock_file.fileno(),
                                fcntl.LOCK_UN,
                            )

                except BlockingIOError:
                    pass

            time.sleep(0.02)

    def stop(self):

        self.running = False
