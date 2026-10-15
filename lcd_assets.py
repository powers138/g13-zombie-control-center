from __future__ import annotations

import base64
import zlib


LCD_BUFFER_SIZE = 960


_ZOMBIE_FRAMEBUFFER_BASE64 = (
    "eNp1UsFtwzAMpOqiyiOAsoGyQfPsjyt5gsoBAvQZdCNt0BW0QfX0w7UryrJMJvZBD/FIHklRABA3"
    "cQGArvPhkQ++A7i0K+FhgWdhLbSbsjld5LewExpPs+w0obWGcP+dCtDCJ3kKjNHGVKs5MFfCkHr0"
    "ceBUES54J/Gq/S18OfLMKhVYLD5QefbQlwLOmgZAFuiFNXIDWVd2p79xGNOZ7z9fB+EbyPpYQwum"
    "upUuxH7OdWi1St3SVvv2zESOQhLl/I1xeSzN2JtIUBmL9Sd8D6Ncmwx9v/kQgoykKiLNIrpJwiHa"
    "U3rtSkQS0UhPHuLK0safRWmM9HXSn1+Z4HmirFfXu4EXtv16WyxVaaXe4Imkq1J7ykmbuRS87kQS"
    "/Q9tACQe"
)


ZOMBIE_FRAMEBUFFER = zlib.decompress(
    base64.b64decode(
        _ZOMBIE_FRAMEBUFFER_BASE64
    )
)


if len(ZOMBIE_FRAMEBUFFER) != LCD_BUFFER_SIZE:
    raise RuntimeError(
        "Invalid ZOMBIE LCD framebuffer size: "
        f"expected {LCD_BUFFER_SIZE}, "
        f"received {len(ZOMBIE_FRAMEBUFFER)}."
    )
