"""A QR code encoder: byte mode, error correction M (raised when it fits
for free), the smallest version that holds the text, the mask with the
lowest penalty. Used for the setup-code QR (:mod:`domovoi.setup_qr`).

A Python port of the Windows tray's encoder (``windows/tray/Qr.cs``),
which is adapted from Project Nayuki's QR Code generator library
(https://www.nayuki.io/page/qr-code-generator-library), byte mode only.
``domovoi/tests/test_setup_qr.py`` checks it module for module against
the matrices in ``windows/tests/QrReference.cs`` (made by an independent
encoder).

  Copyright (c) Project Nayuki. (MIT License)

  Permission is hereby granted, free of charge, to any person obtaining a
  copy of this software and associated documentation files (the
  "Software"), to deal in the Software without restriction, including
  without limitation the rights to use, copy, modify, merge, publish,
  distribute, sublicense, and/or sell copies of the Software, and to
  permit persons to whom the Software is furnished to do so, subject to
  the following conditions:
  - The above copyright notice and this permission notice shall be
    included in all copies or substantial portions of the Software.
  - The Software is provided "as is", without warranty of any kind,
    express or implied, including but not limited to the warranties of
    merchantability, fitness for a particular purpose and
    noninfringement. In no event shall the authors or copyright holders
    be liable for any claim, damages or other liability, whether in an
    action of contract, tort or otherwise, arising from, out of or in
    connection with the Software or the use or other dealings in the
    Software.

Stdlib only.
"""

from __future__ import annotations

# Error correction levels, in the order the tables below use.
LOW, MEDIUM, QUARTILE, HIGH = 0, 1, 2, 3

_FORMAT_BITS = (1, 0, 3, 2)

_ECC_PER_BLOCK = (
    (-1, 7, 10, 15, 20, 26, 18, 20, 24, 30, 18, 20, 24, 26, 30, 22, 24, 28, 30, 28, 28, 28, 28, 30, 30, 26, 28, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30),  # noqa: E501
    (-1, 10, 16, 26, 18, 24, 16, 18, 22, 22, 26, 30, 22, 22, 24, 24, 28, 28, 26, 26, 26, 26, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28),  # noqa: E501
    (-1, 13, 22, 18, 26, 18, 24, 18, 22, 20, 24, 28, 26, 24, 20, 30, 24, 28, 28, 26, 30, 28, 30, 30, 30, 30, 28, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30),  # noqa: E501
    (-1, 17, 28, 22, 16, 22, 28, 26, 26, 24, 28, 24, 28, 22, 24, 24, 30, 28, 28, 26, 28, 30, 24, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30),  # noqa: E501
)

_ECC_BLOCKS = (
    (-1, 1, 1, 1, 1, 1, 2, 2, 2, 2, 4, 4, 4, 4, 4, 6, 6, 6, 6, 7, 8, 8, 9, 9, 10, 12, 12, 12, 13, 14, 15, 16, 17, 18, 19, 19, 20, 21, 22, 24, 25),  # noqa: E501
    (-1, 1, 1, 1, 2, 2, 4, 4, 4, 5, 5, 5, 8, 9, 9, 10, 10, 11, 13, 14, 16, 17, 17, 18, 20, 21, 23, 25, 26, 28, 29, 31, 33, 35, 37, 38, 40, 43, 45, 47, 49),  # noqa: E501
    (-1, 1, 1, 2, 2, 4, 4, 6, 6, 8, 8, 8, 10, 12, 16, 12, 17, 16, 18, 21, 20, 23, 23, 25, 27, 29, 34, 34, 35, 38, 40, 43, 45, 48, 51, 53, 56, 59, 62, 65, 68),  # noqa: E501
    (-1, 1, 1, 2, 4, 4, 4, 5, 6, 8, 8, 11, 11, 16, 16, 18, 16, 19, 21, 25, 25, 25, 34, 30, 32, 35, 37, 40, 42, 45, 48, 51, 54, 57, 60, 63, 66, 70, 74, 77, 81),  # noqa: E501
)


def _bit(x: int, i: int) -> bool:
    return ((x >> i) & 1) != 0


def _raw_data_modules(ver: int) -> int:
    result = (16 * ver + 128) * ver + 64
    if ver >= 2:
        n = ver // 7 + 2
        result -= (25 * n - 10) * n - 55
        if ver >= 7:
            result -= 36
    return result


def _data_codewords(ver: int, ecc: int) -> int:
    return _raw_data_modules(ver) // 8 - _ECC_PER_BLOCK[ecc][ver] * _ECC_BLOCKS[ecc][ver]


def _rs_multiply(x: int, y: int) -> int:
    z = 0
    for i in range(7, -1, -1):
        z = (z << 1) ^ ((z >> 7) * 0x11D)
        z ^= ((y >> i) & 1) * x
    return z


def _rs_divisor(degree: int) -> list[int]:
    result = [0] * degree
    result[degree - 1] = 1
    root = 1
    for _ in range(degree):
        for j in range(degree):
            result[j] = _rs_multiply(result[j], root)
            if j + 1 < degree:
                result[j] ^= result[j + 1]
        root = _rs_multiply(root, 0x02)
    return result


def _rs_remainder(data: list[int], divisor: list[int]) -> list[int]:
    result = [0] * len(divisor)
    for b in data:
        factor = b ^ result[0]
        result = result[1:] + [0]
        for i in range(len(result)):
            result[i] ^= _rs_multiply(divisor[i], factor)
    return result


class QrCode:
    """One encoded symbol. ``dark(x, y)`` reads a module; :meth:`text`
    gives the matrix, one row per line, ``#`` dark and ``.`` light."""

    def __init__(self, version: int, ecc: int, data_codewords: list[int], mask: int) -> None:
        self.version = version
        self.ecc = ecc
        self.size = version * 4 + 17
        n = self.size
        self._modules = [[False] * n for _ in range(n)]  # [y][x]
        self._is_function = [[False] * n for _ in range(n)]
        self._draw_function_patterns()
        self._draw_codewords(self._add_ecc_and_interleave(data_codewords))
        if mask == -1:
            best = None
            for i in range(8):
                self._apply_mask(i)
                self._draw_format_bits(i)
                p = self._penalty()
                if best is None or p < best:
                    mask, best = i, p
                self._apply_mask(i)
        self.mask = mask
        self._apply_mask(mask)
        self._draw_format_bits(mask)
        self._is_function = None

    # ── building ──────────────────────────────────────────────────────

    @classmethod
    def encode_text(cls, text: str, ecc: int = MEDIUM, mask: int = -1, boost: bool = True) -> QrCode:
        return cls.encode_bytes(text.encode("utf-8"), ecc, mask, boost)

    @classmethod
    def encode_bytes(cls, data: bytes, ecc: int = MEDIUM, mask: int = -1, boost: bool = True,
                     min_version: int = 1, max_version: int = 40) -> QrCode:
        version = min_version
        while True:
            cc_bits = 8 if version <= 9 else 16
            used = 4 + cc_bits + len(data) * 8 if len(data) < (1 << cc_bits) else -1
            if 0 <= used <= _data_codewords(version, ecc) * 8:
                break
            if version >= max_version:
                raise ValueError("text too long for a QR code")
            version += 1
        if boost:
            for e in (MEDIUM, QUARTILE, HIGH):
                if used <= _data_codewords(version, e) * 8:
                    ecc = e
        bits: list[int] = []

        def append(val: int, length: int) -> None:
            for i in range(length - 1, -1, -1):
                bits.append((val >> i) & 1)

        append(0x4, 4)
        append(len(data), 8 if version <= 9 else 16)
        for b in data:
            append(b, 8)
        capacity = _data_codewords(version, ecc) * 8
        append(0, min(4, capacity - len(bits)))
        append(0, (8 - len(bits) % 8) % 8)
        pad = 0xEC
        while len(bits) < capacity:
            append(pad, 8)
            pad ^= 0xEC ^ 0x11
        codewords = [0] * (len(bits) // 8)
        for i, bit in enumerate(bits):
            if bit:
                codewords[i >> 3] |= 1 << (7 - (i & 7))
        return cls(version, ecc, codewords, mask)

    def _set(self, x: int, y: int, dark: bool) -> None:
        self._modules[y][x] = dark
        self._is_function[y][x] = True

    def _draw_function_patterns(self) -> None:
        n = self.size
        for i in range(n):
            self._set(6, i, i % 2 == 0)
            self._set(i, 6, i % 2 == 0)
        self._draw_finder(3, 3)
        self._draw_finder(n - 4, 3)
        self._draw_finder(3, n - 4)
        pos = self._alignment_positions()
        k = len(pos)
        for i in range(k):
            for j in range(k):
                if not ((i == 0 and j == 0) or (i == 0 and j == k - 1) or (i == k - 1 and j == 0)):
                    self._draw_alignment(pos[i], pos[j])
        self._draw_format_bits(0)
        self._draw_version()

    def _draw_format_bits(self, mask: int) -> None:
        data = _FORMAT_BITS[self.ecc] << 3 | mask
        rem = data
        for _ in range(10):
            rem = (rem << 1) ^ ((rem >> 9) * 0x537)
        bits = (data << 10 | rem) ^ 0x5412
        n = self.size
        for i in range(6):
            self._set(8, i, _bit(bits, i))
        self._set(8, 7, _bit(bits, 6))
        self._set(8, 8, _bit(bits, 7))
        self._set(7, 8, _bit(bits, 8))
        for i in range(9, 15):
            self._set(14 - i, 8, _bit(bits, i))
        for i in range(8):
            self._set(n - 1 - i, 8, _bit(bits, i))
        for i in range(8, 15):
            self._set(8, n - 15 + i, _bit(bits, i))
        self._set(8, n - 8, True)

    def _draw_version(self) -> None:
        if self.version < 7:
            return
        rem = self.version
        for _ in range(12):
            rem = (rem << 1) ^ ((rem >> 11) * 0x1F25)
        bits = self.version << 12 | rem
        for i in range(18):
            bit = _bit(bits, i)
            a, b = self.size - 11 + i % 3, i // 3
            self._set(a, b, bit)
            self._set(b, a, bit)

    def _draw_finder(self, x: int, y: int) -> None:
        for dy in range(-4, 5):
            for dx in range(-4, 5):
                dist = max(abs(dx), abs(dy))
                xx, yy = x + dx, y + dy
                if 0 <= xx < self.size and 0 <= yy < self.size:
                    self._set(xx, yy, dist not in (2, 4))

    def _draw_alignment(self, x: int, y: int) -> None:
        for dy in range(-2, 3):
            for dx in range(-2, 3):
                self._set(x + dx, y + dy, max(abs(dx), abs(dy)) != 1)

    def _add_ecc_and_interleave(self, data: list[int]) -> list[int]:
        num_blocks = _ECC_BLOCKS[self.ecc][self.version]
        ecc_len = _ECC_PER_BLOCK[self.ecc][self.version]
        raw = _raw_data_modules(self.version) // 8
        num_short = num_blocks - raw % num_blocks
        short_len = raw // num_blocks
        div = _rs_divisor(ecc_len)
        blocks: list[list[int]] = []
        k = 0
        for i in range(num_blocks):
            dat_len = short_len - ecc_len + (0 if i < num_short else 1)
            dat = data[k:k + dat_len]
            k += dat_len
            ecc = _rs_remainder(dat, div)
            # A short block has a placeholder byte before its ECC.
            block = dat + [0] * (short_len + 1 - dat_len - ecc_len) + ecc
            blocks.append(block)
        result: list[int] = []
        for i in range(short_len + 1):
            for j, block in enumerate(blocks):
                if i != short_len - ecc_len or j >= num_short:
                    result.append(block[i])
        return result

    def _draw_codewords(self, data: list[int]) -> None:
        n = self.size
        i = 0
        right = n - 1
        while right >= 1:
            if right == 6:
                right = 5
            for vert in range(n):
                for j in range(2):
                    x = right - j
                    upward = ((right + 1) & 2) == 0
                    y = n - 1 - vert if upward else vert
                    if not self._is_function[y][x] and i < len(data) * 8:
                        self._modules[y][x] = _bit(data[i >> 3], 7 - (i & 7))
                        i += 1
            right -= 2

    def _apply_mask(self, mask: int) -> None:
        if not 0 <= mask <= 7:
            raise ValueError("mask")
        for y in range(self.size):
            row, fn = self._modules[y], self._is_function[y]
            for x in range(self.size):
                if mask == 0:
                    invert = (x + y) % 2 == 0
                elif mask == 1:
                    invert = y % 2 == 0
                elif mask == 2:
                    invert = x % 3 == 0
                elif mask == 3:
                    invert = (x + y) % 3 == 0
                elif mask == 4:
                    invert = (x // 3 + y // 2) % 2 == 0
                elif mask == 5:
                    invert = x * y % 2 + x * y % 3 == 0
                elif mask == 6:
                    invert = (x * y % 2 + x * y % 3) % 2 == 0
                else:
                    invert = ((x + y) % 2 + x * y % 3) % 2 == 0
                if invert and not fn[x]:
                    row[x] = not row[x]

    def _penalty(self) -> int:
        n = self.size
        m = self._modules
        result = 0
        for pass_ in range(2):
            for a in range(n):
                run_color = False
                run = 0
                history = [0] * 7
                for b in range(n):
                    cell = m[a][b] if pass_ == 0 else m[b][a]
                    if cell == run_color:
                        run += 1
                        if run == 5:
                            result += 3
                        elif run > 5:
                            result += 1
                    else:
                        self._add_history(run, history)
                        if not run_color:
                            result += self._count_patterns(history) * 40
                        run_color = cell
                        run = 1
                result += self._terminate_and_count(run_color, run, history) * 40
        for y in range(n - 1):
            for x in range(n - 1):
                c = m[y][x]
                if c == m[y][x + 1] and c == m[y + 1][x] and c == m[y + 1][x + 1]:
                    result += 3
        dark = sum(row.count(True) for row in m)
        total = n * n
        k = (abs(dark * 20 - total * 10) + total - 1) // total - 1
        result += k * 10
        return result

    @staticmethod
    def _count_patterns(h: list[int]) -> int:
        n = h[1]
        core = n > 0 and h[2] == n and h[3] == n * 3 and h[4] == n and h[5] == n
        return (1 if core and h[0] >= n * 4 and h[6] >= n else 0) + \
            (1 if core and h[6] >= n * 4 and h[0] >= n else 0)

    def _terminate_and_count(self, run_color: bool, run: int, h: list[int]) -> int:
        if run_color:
            self._add_history(run, h)
            run = 0
        run += self.size
        self._add_history(run, h)
        return self._count_patterns(h)

    def _add_history(self, run: int, h: list[int]) -> None:
        if h[0] == 0:
            run += self.size
        h[1:] = h[:-1]
        h[0] = run

    def _alignment_positions(self) -> list[int]:
        if self.version == 1:
            return []
        n = self.version // 7 + 2
        step = (self.version * 8 + n * 3 + 5) // (n * 4 - 4) * 2
        result = [0] * n
        result[0] = 6
        pos = self.size - 7
        for i in range(n - 1, 0, -1):
            result[i] = pos
            pos -= step
        return result

    # ── reading ───────────────────────────────────────────────────────

    def dark(self, x: int, y: int) -> bool:
        return 0 <= x < self.size and 0 <= y < self.size and self._modules[y][x]

    def text(self) -> str:
        """One line per row, ``#`` dark and ``.`` light: what the tests
        compare."""
        return "".join("".join("#" if c else "." for c in row) + "\n" for row in self._modules)
