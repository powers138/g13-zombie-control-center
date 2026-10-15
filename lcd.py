from __future__ import annotations
from lcd_compact_font import (
    FONT_ADVANCE,
    FONT_HEIGHT,
    FONT_WIDTH,
    get_glyph as get_compact_glyph,
)

from lcd_assets import ZOMBIE_FRAMEBUFFER
from lcd_font import (
    MONOGRAM_ADVANCE,
    MONOGRAM_GLYPH_HEIGHT,
    MONOGRAM_GLYPH_WIDTH,
    get_monogram_glyph,
    monogram_text_width,
)


LCD_WIDTH = 160
LCD_HEIGHT = 48

LCD_BUFFER_SIZE = 960
LCD_TRANSFER_SIZE = 992
LCD_FRAME_OFFSET = 32
LCD_REPORT_ID = 0x03


class G13LCD:
    """
    Controls the Logitech G13's 160 × 48 monochrome LCD.
    """

    def __init__(self, device):
        self.device = device

        self.framebuffer = bytearray(
            LCD_BUFFER_SIZE
        )

    def initialize(self) -> None:
        report = bytes(
            (
                0x00,
                0x01,
            )
        )

        written = (
            self.device.send_feature_report(
                report
            )
        )

        if written != len(report):
            raise RuntimeError(
                "Incomplete LCD initialization: "
                f"wrote {written} of "
                f"{len(report)} bytes."
            )

    def clear_buffer(self) -> None:
        self.framebuffer[:] = (
            b"\x00" * LCD_BUFFER_SIZE
        )

    def clear(self) -> None:
        self.clear_buffer()
        self.update()

    def load_framebuffer(
        self,
        framebuffer: bytes | bytearray,
    ) -> None:
        if len(framebuffer) != LCD_BUFFER_SIZE:
            raise ValueError(
                "LCD framebuffer must contain "
                f"exactly {LCD_BUFFER_SIZE} bytes."
            )

        self.framebuffer[:] = framebuffer

    def show_zombie(self) -> None:
        self.load_framebuffer(
            ZOMBIE_FRAMEBUFFER
        )

        self.update()

    def set_pixel(
        self,
        x: int,
        y: int,
        enabled: bool = True,
    ) -> None:
        if not (
            0 <= x < LCD_WIDTH
            and 0 <= y < LCD_HEIGHT
        ):
            return

        byte_index = (
            x
            + (y // 8) * LCD_WIDTH
        )

        bit_mask = 1 << (y % 8)

        if enabled:
            self.framebuffer[
                byte_index
            ] |= bit_mask
        else:
            self.framebuffer[
                byte_index
            ] &= ~bit_mask

    def draw_horizontal_line(
        self,
        y: int,
        start_x: int = 0,
        end_x: int = LCD_WIDTH - 1,
        enabled: bool = True,
    ) -> None:
        start_x = max(0, start_x)
        end_x = min(
            LCD_WIDTH - 1,
            end_x,
        )

        if start_x > end_x:
            return

        for x in range(
            start_x,
            end_x + 1,
        ):
            self.set_pixel(
                x,
                y,
                enabled,
            )

    def draw_vertical_line(
        self,
        x: int,
        start_y: int = 0,
        end_y: int = LCD_HEIGHT - 1,
        enabled: bool = True,
    ) -> None:
        start_y = max(0, start_y)
        end_y = min(
            LCD_HEIGHT - 1,
            end_y,
        )

        if start_y > end_y:
            return

        for y in range(
            start_y,
            end_y + 1,
        ):
            self.set_pixel(
                x,
                y,
                enabled,
            )

    def draw_box(
        self,
        left: int,
        top: int,
        right: int,
        bottom: int,
        enabled: bool = True,
    ) -> None:
        self.draw_horizontal_line(
            top,
            left,
            right,
            enabled,
        )

        self.draw_horizontal_line(
            bottom,
            left,
            right,
            enabled,
        )

        self.draw_vertical_line(
            left,
            top,
            bottom,
            enabled,
        )

        self.draw_vertical_line(
            right,
            top,
            bottom,
            enabled,
        )

    def fill_box(
        self,
        left: int,
        top: int,
        right: int,
        bottom: int,
        enabled: bool = True,
    ) -> None:
        left = max(0, left)
        top = max(0, top)

        right = min(
            LCD_WIDTH - 1,
            right,
        )

        bottom = min(
            LCD_HEIGHT - 1,
            bottom,
        )

        if left > right or top > bottom:
            return

        for y in range(
            top,
            bottom + 1,
        ):
            self.draw_horizontal_line(
                y,
                left,
                right,
                enabled,
            )

    @staticmethod
    def text_width(
        text: str,
    ) -> int:
        return monogram_text_width(text)

    def draw_character(
        self,
        character: str,
        x: int,
        y: int,
        inverted: bool = False,
    ) -> None:
        glyph = get_monogram_glyph(
            character
        )

        if inverted:
            self.fill_box(
                x,
                y,
                x + MONOGRAM_ADVANCE - 1,
                y + MONOGRAM_GLYPH_HEIGHT - 1,
                True,
            )

        for row_index, row_bits in enumerate(
            glyph
        ):
            for column in range(
                MONOGRAM_GLYPH_WIDTH
            ):
                bit_mask = 1 << column

                if not row_bits & bit_mask:
                    continue

                self.set_pixel(
                    x + column,
                    y + row_index,
                    not inverted,
                )

    def draw_text(
        self,
        text: str,
        x: int,
        y: int,
        inverted: bool = False,
        maximum_width: int | None = None,
    ) -> int:
        """
        Draw Monogram text into the local framebuffer.

        Returns the final horizontal cursor position.
        """
        start_x = x
        cursor_x = x
        cursor_y = y

        width_limit = (
            LCD_WIDTH - start_x
            if maximum_width is None
            else maximum_width
        )

        right_limit = (
            start_x
            + max(0, width_limit)
        )

        for character in text:
            if character == "\n":
                cursor_x = start_x
                cursor_y += (
                    MONOGRAM_GLYPH_HEIGHT
                )
                continue

            if (
                cursor_y
                + MONOGRAM_GLYPH_HEIGHT
                > LCD_HEIGHT
            ):
                break

            if (
                cursor_x
                + MONOGRAM_ADVANCE
                > right_limit
            ):
                break

            self.draw_character(
                character,
                cursor_x,
                cursor_y,
                inverted,
            )

            cursor_x += MONOGRAM_ADVANCE

        return cursor_x

    def draw_compact_character(
        self,
        character: str,
        x: int,
        y: int,
        inverted: bool = False,
    ) -> None:
        glyph = get_compact_glyph(
            character
        )

        if inverted:
            self.fill_box(
                x,
                y,
                x + FONT_ADVANCE - 1,
                y + FONT_HEIGHT - 1,
                True,
            )

        for row_index, row_bits in enumerate(
            glyph
        ):
            for column in range(
                FONT_WIDTH
            ):
                bit_mask = (
                    1
                    << (
                        FONT_WIDTH
                        - 1
                        - column
                    )
                )

                if not row_bits & bit_mask:
                    continue

                self.set_pixel(
                    x + column,
                    y + row_index,
                    not inverted,
                )

    def draw_compact_text(
        self,
        text: str,
        x: int,
        y: int,
        inverted: bool = False,
        maximum_width: int | None = None,
    ) -> int:
        cursor_x = x

        if maximum_width is None:
            right_limit = LCD_WIDTH
        else:
            right_limit = (
                x + max(0, maximum_width)
            )

        for character in text:
            if (
                cursor_x + FONT_ADVANCE
                > right_limit
            ):
                break

            self.draw_compact_character(
                character,
                cursor_x,
                y,
                inverted,
            )

            cursor_x += FONT_ADVANCE

        return cursor_x

    def update(self) -> None:
        transfer = bytearray(
            LCD_TRANSFER_SIZE
        )

        transfer[0] = LCD_REPORT_ID

        transfer[
            LCD_FRAME_OFFSET:
            LCD_FRAME_OFFSET + LCD_BUFFER_SIZE
        ] = self.framebuffer

        written = self.device.write(
            bytes(transfer)
        )

        if written != LCD_TRANSFER_SIZE:
            raise RuntimeError(
                "Incomplete LCD output: "
                f"wrote {written} of "
                f"{LCD_TRANSFER_SIZE} bytes."
            )
