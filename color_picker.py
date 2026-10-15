from __future__ import annotations

import math

from PySide6.QtCore import (
    QPointF,
    QRectF,
    QSize,
    Qt,
    Signal,
)
from PySide6.QtGui import (
    QColor,
    QConicalGradient,
    QLinearGradient,
    QMouseEvent,
    QPainter,
    QPainterPath,
    QPen,
)
from PySide6.QtWidgets import (
    QWidget,
)


class HueWheel(QWidget):
    colorChanged = Signal(QColor)

    RING_WIDTH = 34.0
    OUTER_MARGIN = 12.0

    def __init__(
        self,
        color: QColor,
        parent=None,
    ):
        super().__init__(parent)

        self._color = QColor(color)

        if self._color.hsvHue() < 0:
            self._color.setHsv(
                0,
                self._color.hsvSaturation(),
                self._color.value(),
            )

        self.setMinimumSize(360, 360)
        self.setMouseTracking(True)

        self._drag_mode: str | None = None

    def sizeHint(self) -> QSize:
        return QSize(420, 420)

    def color(self) -> QColor:
        return QColor(self._color)

    def setColor(
        self,
        color: QColor,
        emit_signal: bool = True,
    ) -> None:
        new_color = QColor(color)

        if new_color.hsvHue() < 0:
            new_color.setHsv(
                self._color.hsvHue(),
                new_color.hsvSaturation(),
                new_color.value(),
            )

        if new_color == self._color:
            return

        self._color = new_color
        self.update()

        if emit_signal:
            self.colorChanged.emit(
                QColor(self._color)
            )

    def _geometry(
        self,
    ) -> tuple[
        QPointF,
        float,
        float,
        QRectF,
    ]:
        center = QPointF(
            self.width() / 2.0,
            self.height() / 2.0,
        )

        outer_radius = (
            min(
                self.width(),
                self.height(),
            )
            / 2.0
            - self.OUTER_MARGIN
        )

        inner_radius = (
            outer_radius
            - self.RING_WIDTH
        )

        # The square fits completely inside the inner circle.
        square_side = inner_radius * 1.25

        square = QRectF(
            center.x() - square_side / 2.0,
            center.y() - square_side / 2.0,
            square_side,
            square_side,
        )

        return (
            center,
            outer_radius,
            inner_radius,
            square,
        )

    def paintEvent(self, event) -> None:
        del event

        painter = QPainter(self)

        painter.setRenderHint(
            QPainter.RenderHint.Antialiasing,
            True,
        )

        (
            center,
            outer_radius,
            inner_radius,
            square,
        ) = self._geometry()

        self._draw_hue_ring(
            painter,
            center,
            outer_radius,
            inner_radius,
        )

        self._draw_sv_square(
            painter,
            square,
        )

        self._draw_markers(
            painter,
            center,
            outer_radius,
            inner_radius,
            square,
        )

    def _draw_hue_ring(
        self,
        painter: QPainter,
        center: QPointF,
        outer_radius: float,
        inner_radius: float,
    ) -> None:
        gradient = QConicalGradient(
            center,
            0.0,
        )

        for hue in range(
            0,
            361,
            30,
        ):
            gradient.setColorAt(
                hue / 360.0,
                QColor.fromHsv(
                    hue % 360,
                    255,
                    255,
                ),
            )

        outer_path = QPainterPath()
        outer_path.addEllipse(
            center,
            outer_radius,
            outer_radius,
        )

        inner_path = QPainterPath()
        inner_path.addEllipse(
            center,
            inner_radius,
            inner_radius,
        )

        ring_path = outer_path.subtracted(
            inner_path
        )

        painter.fillPath(
            ring_path,
            gradient,
        )

        painter.setPen(
            QPen(
                QColor("#777777"),
                1.0,
            )
        )

        painter.drawEllipse(
            center,
            outer_radius,
            outer_radius,
        )

        painter.drawEllipse(
            center,
            inner_radius,
            inner_radius,
        )

    def _draw_sv_square(
        self,
        painter: QPainter,
        square: QRectF,
    ) -> None:
        hue = self._color.hsvHue()

        if hue < 0:
            hue = 0

        painter.fillRect(
            square,
            QColor.fromHsv(
                hue,
                255,
                255,
            ),
        )

        saturation_gradient = QLinearGradient(
            square.topLeft(),
            square.topRight(),
        )

        saturation_gradient.setColorAt(
            0.0,
            QColor(
                255,
                255,
                255,
                255,
            ),
        )

        saturation_gradient.setColorAt(
            1.0,
            QColor(
                255,
                255,
                255,
                0,
            ),
        )

        painter.fillRect(
            square,
            saturation_gradient,
        )

        value_gradient = QLinearGradient(
            square.topLeft(),
            square.bottomLeft(),
        )

        value_gradient.setColorAt(
            0.0,
            QColor(
                0,
                0,
                0,
                0,
            ),
        )

        value_gradient.setColorAt(
            1.0,
            QColor(
                0,
                0,
                0,
                255,
            ),
        )

        painter.fillRect(
            square,
            value_gradient,
        )

        painter.setPen(
            QPen(
                QColor("#999999"),
                1.0,
            )
        )

        painter.drawRect(square)

    def _draw_markers(
        self,
        painter: QPainter,
        center: QPointF,
        outer_radius: float,
        inner_radius: float,
        square: QRectF,
    ) -> None:
        hue = self._color.hsvHue()

        if hue < 0:
            hue = 0

        angle = math.radians(hue)

        ring_radius = (
            outer_radius
            + inner_radius
        ) / 2.0

        hue_point = QPointF(
            center.x()
            + math.cos(angle) * ring_radius,
            center.y()
            - math.sin(angle) * ring_radius,
        )

        painter.setBrush(
            QColor(self._color)
        )

        painter.setPen(
            QPen(
                QColor("#FFFFFF"),
                3.0,
            )
        )

        painter.drawEllipse(
            hue_point,
            8.0,
            8.0,
        )

        saturation = (
            self._color.hsvSaturation()
            / 255.0
        )

        value = (
            self._color.value()
            / 255.0
        )

        sv_point = QPointF(
            square.left()
            + saturation * square.width(),
            square.top()
            + (1.0 - value) * square.height(),
        )

        painter.setBrush(
            QColor(self._color)
        )

        painter.setPen(
            QPen(
                QColor("#000000"),
                4.0,
            )
        )

        painter.drawEllipse(
            sv_point,
            7.0,
            7.0,
        )

        painter.setPen(
            QPen(
                QColor("#FFFFFF"),
                2.0,
            )
        )

        painter.drawEllipse(
            sv_point,
            7.0,
            7.0,
        )

    def mousePressEvent(
        self,
        event: QMouseEvent,
    ) -> None:
        if (
            event.button()
            != Qt.MouseButton.LeftButton
        ):
            super().mousePressEvent(
                event
            )
            return

        (
            center,
            outer_radius,
            inner_radius,
            square,
        ) = self._geometry()

        position = event.position()

        saturation = (
            self._color.hsvSaturation()
            / 255.0
        )

        value = (
            self._color.value()
            / 255.0
        )

        sv_point = QPointF(
            square.left()
            + saturation * square.width(),
            square.top()
            + (1.0 - value) * square.height(),
        )

        marker_distance = math.hypot(
            position.x()
            - sv_point.x(),
            position.y()
            - sv_point.y(),
        )

        sv_marker_hit = (
            marker_distance
            <= 11.0
        )

        if (
            square.contains(
                position
            )
            or sv_marker_hit
        ):
            self._drag_mode = "sv"

        else:
            dx = (
                position.x()
                - center.x()
            )

            dy = (
                position.y()
                - center.y()
            )

            distance = math.hypot(
                dx,
                dy,
            )

            if (
                inner_radius
                <= distance
                <= outer_radius
            ):
                self._drag_mode = "hue"

            else:
                self._drag_mode = None

        if self._drag_mode is None:
            super().mousePressEvent(
                event
            )
            return

        self._update_from_position(
            position,
            self._drag_mode,
        )

        event.accept()

    def mouseMoveEvent(
        self,
        event: QMouseEvent,
    ) -> None:
        if (
            not (
                event.buttons()
                & Qt.MouseButton.LeftButton
            )
            or self._drag_mode is None
        ):
            super().mouseMoveEvent(
                event
            )
            return

        self._update_from_position(
            event.position(),
            self._drag_mode,
        )

        event.accept()

    def mouseReleaseEvent(
        self,
        event: QMouseEvent,
    ) -> None:
        if (
            event.button()
            == Qt.MouseButton.LeftButton
            and self._drag_mode is not None
        ):
            self._drag_mode = None

            event.accept()
            return

        super().mouseReleaseEvent(
            event
        )

    def _update_from_position(
        self,
        position: QPointF,
        drag_mode: str,
    ) -> None:
        (
            center,
            _outer_radius,
            _inner_radius,
            square,
        ) = self._geometry()

        if drag_mode == "hue":
            dx = (
                position.x()
                - center.x()
            )

            dy = (
                position.y()
                - center.y()
            )

            if (
                abs(dx) < 0.001
                and abs(dy) < 0.001
            ):
                return

            angle = math.degrees(
                math.atan2(
                    -dy,
                    dx,
                )
            )

            hue = round(
                angle % 360.0
            )

            self._color.setHsv(
                hue,
                self._color.hsvSaturation(),
                self._color.value(),
            )

            self.update()

            self.colorChanged.emit(
                QColor(self._color)
            )

            return

        if drag_mode != "sv":
            return

        x = max(
            square.left(),
            min(
                square.right(),
                position.x(),
            ),
        )

        y = max(
            square.top(),
            min(
                square.bottom(),
                position.y(),
            ),
        )

        saturation = round(
            (
                x
                - square.left()
            )
            / square.width()
            * 255
        )

        value = round(
            (
                1.0
                - (
                    y
                    - square.top()
                )
                / square.height()
            )
            * 255
        )

        saturation = max(
            0,
            min(
                255,
                saturation,
            ),
        )

        value = max(
            0,
            min(
                255,
                value,
            ),
        )

        hue = self._color.hsvHue()

        if hue < 0:
            hue = 0

        self._color.setHsv(
            hue,
            saturation,
            value,
        )

        self.update()

        self.colorChanged.emit(
            QColor(self._color)
        )
