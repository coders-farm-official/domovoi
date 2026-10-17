// A QR code for "Show a code for your phone": byte mode, error correction
// M (raised when it fits for free), the smallest version that holds the
// text, the mask with the lowest penalty.
//
// Adapted to C# 5 from Project Nayuki's QR Code generator library
// (https://www.nayuki.io/page/qr-code-generator-library), byte mode only:
//
//   Copyright (c) Project Nayuki. (MIT License)
//
//   Permission is hereby granted, free of charge, to any person obtaining a
//   copy of this software and associated documentation files (the
//   "Software"), to deal in the Software without restriction, including
//   without limitation the rights to use, copy, modify, merge, publish,
//   distribute, sublicense, and/or sell copies of the Software, and to
//   permit persons to whom the Software is furnished to do so, subject to
//   the following conditions:
//   - The above copyright notice and this permission notice shall be
//     included in all copies or substantial portions of the Software.
//   - The Software is provided "as is", without warranty of any kind,
//     express or implied, including but not limited to the warranties of
//     merchantability, fitness for a particular purpose and
//     noninfringement. In no event shall the authors or copyright holders
//     be liable for any claim, damages or other liability, whether in an
//     action of contract, tort or otherwise, arising from, out of or in
//     connection with the Software or the use or other dealings in the
//     Software.

using System;
using System.Collections.Generic;
using System.Text;

namespace Domovoi.Windows.Tray
{
    public enum QrEcc { Low = 0, Medium = 1, Quartile = 2, High = 3 }

    public sealed class QrCode
    {
        public readonly int Version, Size, Mask;
        public readonly QrEcc Ecc;
        readonly bool[,] modules;     // [y, x], true = dark
        bool[,] isFunction;

        public bool Dark(int x, int y) { return x >= 0 && y >= 0 && x < Size && y < Size && modules[y, x]; }

        static readonly int[] FormatBits = { 1, 0, 3, 2 };

        static readonly int[,] EccPerBlock = {
            { -1, 7, 10, 15, 20, 26, 18, 20, 24, 30, 18, 20, 24, 26, 30, 22, 24, 28, 30, 28, 28, 28, 28, 30, 30, 26, 28, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30 },
            { -1, 10, 16, 26, 18, 24, 16, 18, 22, 22, 26, 30, 22, 22, 24, 24, 28, 28, 26, 26, 26, 26, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28 },
            { -1, 13, 22, 18, 26, 18, 24, 18, 22, 20, 24, 28, 26, 24, 20, 30, 24, 28, 28, 26, 30, 28, 30, 30, 30, 30, 28, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30 },
            { -1, 17, 28, 22, 16, 22, 28, 26, 26, 24, 28, 24, 28, 22, 24, 24, 30, 28, 28, 26, 28, 30, 24, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30 },
        };

        static readonly int[,] EccBlocks = {
            { -1, 1, 1, 1, 1, 1, 2, 2, 2, 2, 4, 4, 4, 4, 4, 6, 6, 6, 6, 7, 8, 8, 9, 9, 10, 12, 12, 12, 13, 14, 15, 16, 17, 18, 19, 19, 20, 21, 22, 24, 25 },
            { -1, 1, 1, 1, 2, 2, 4, 4, 4, 5, 5, 5, 8, 9, 9, 10, 10, 11, 13, 14, 16, 17, 17, 18, 20, 21, 23, 25, 26, 28, 29, 31, 33, 35, 37, 38, 40, 43, 45, 47, 49 },
            { -1, 1, 1, 2, 2, 4, 4, 6, 6, 8, 8, 8, 10, 12, 16, 12, 17, 16, 18, 21, 20, 23, 23, 25, 27, 29, 34, 34, 35, 38, 40, 43, 45, 48, 51, 53, 56, 59, 62, 65, 68 },
            { -1, 1, 1, 2, 4, 4, 4, 5, 6, 8, 8, 11, 11, 16, 16, 18, 16, 19, 21, 25, 25, 25, 34, 30, 32, 35, 37, 40, 42, 45, 48, 51, 54, 57, 60, 63, 66, 70, 74, 77, 81 },
        };

        // The text as UTF-8 bytes. mask -1: the best one; boost: raise the
        // error correction while the version stays the same.
        public static QrCode EncodeText(string text, QrEcc ecc, int mask, bool boost)
        {
            return EncodeBytes(Encoding.UTF8.GetBytes(text), ecc, mask, boost, 1, 40);
        }

        public static QrCode EncodeBytes(byte[] data, QrEcc ecc, int mask, bool boost, int minVersion, int maxVersion)
        {
            int version, used = -1;
            for (version = minVersion; ; version++)
            {
                int ccBits = version <= 9 ? 8 : 16;
                if (data.Length < (1 << ccBits)) used = 4 + ccBits + data.Length * 8;
                else used = -1;
                if (used >= 0 && used <= DataCodewords(version, ecc) * 8) break;
                if (version >= maxVersion) throw new ArgumentException("text too long for a QR code");
            }
            if (boost)
                foreach (QrEcc e in new[] { QrEcc.Medium, QrEcc.Quartile, QrEcc.High })
                    if (used <= DataCodewords(version, e) * 8) ecc = e;

            var bits = new List<bool>();
            Append(bits, 0x4, 4);
            Append(bits, data.Length, version <= 9 ? 8 : 16);
            foreach (byte b in data) Append(bits, b, 8);
            int capacity = DataCodewords(version, ecc) * 8;
            Append(bits, 0, Math.Min(4, capacity - bits.Count));
            Append(bits, 0, (8 - bits.Count % 8) % 8);
            for (int pad = 0xEC; bits.Count < capacity; pad ^= 0xEC ^ 0x11) Append(bits, pad, 8);
            var codewords = new byte[bits.Count / 8];
            for (int i = 0; i < bits.Count; i++) if (bits[i]) codewords[i >> 3] |= (byte)(1 << (7 - (i & 7)));
            return new QrCode(version, ecc, codewords, mask);
        }

        static void Append(List<bool> bits, int val, int len)
        {
            for (int i = len - 1; i >= 0; i--) bits.Add(((val >> i) & 1) != 0);
        }

        QrCode(int version, QrEcc ecc, byte[] dataCodewords, int mask)
        {
            Version = version;
            Ecc = ecc;
            Size = version * 4 + 17;
            modules = new bool[Size, Size];
            isFunction = new bool[Size, Size];
            DrawFunctionPatterns();
            DrawCodewords(AddEccAndInterleave(dataCodewords));
            if (mask == -1)
            {
                int min = int.MaxValue;
                for (int i = 0; i < 8; i++)
                {
                    ApplyMask(i);
                    DrawFormatBits(i);
                    int p = Penalty();
                    if (p < min) { mask = i; min = p; }
                    ApplyMask(i);
                }
            }
            Mask = mask;
            ApplyMask(mask);
            DrawFormatBits(mask);
            isFunction = null;
        }

        void Set(int x, int y, bool dark)
        {
            modules[y, x] = dark;
            isFunction[y, x] = true;
        }

        void DrawFunctionPatterns()
        {
            for (int i = 0; i < Size; i++)
            {
                Set(6, i, i % 2 == 0);
                Set(i, 6, i % 2 == 0);
            }
            DrawFinder(3, 3);
            DrawFinder(Size - 4, 3);
            DrawFinder(3, Size - 4);
            int[] pos = AlignmentPositions();
            int n = pos.Length;
            for (int i = 0; i < n; i++)
                for (int j = 0; j < n; j++)
                    if (!((i == 0 && j == 0) || (i == 0 && j == n - 1) || (i == n - 1 && j == 0)))
                        DrawAlignment(pos[i], pos[j]);
            DrawFormatBits(0);
            DrawVersion();
        }

        void DrawFormatBits(int mask)
        {
            int data = FormatBits[(int)Ecc] << 3 | mask;
            int rem = data;
            for (int i = 0; i < 10; i++) rem = (rem << 1) ^ ((rem >> 9) * 0x537);
            int bits = (data << 10 | rem) ^ 0x5412;
            for (int i = 0; i <= 5; i++) Set(8, i, Bit(bits, i));
            Set(8, 7, Bit(bits, 6));
            Set(8, 8, Bit(bits, 7));
            Set(7, 8, Bit(bits, 8));
            for (int i = 9; i < 15; i++) Set(14 - i, 8, Bit(bits, i));
            for (int i = 0; i < 8; i++) Set(Size - 1 - i, 8, Bit(bits, i));
            for (int i = 8; i < 15; i++) Set(8, Size - 15 + i, Bit(bits, i));
            Set(8, Size - 8, true);
        }

        void DrawVersion()
        {
            if (Version < 7) return;
            int rem = Version;
            for (int i = 0; i < 12; i++) rem = (rem << 1) ^ ((rem >> 11) * 0x1F25);
            int bits = Version << 12 | rem;
            for (int i = 0; i < 18; i++)
            {
                bool bit = Bit(bits, i);
                int a = Size - 11 + i % 3, b = i / 3;
                Set(a, b, bit);
                Set(b, a, bit);
            }
        }

        void DrawFinder(int x, int y)
        {
            for (int dy = -4; dy <= 4; dy++)
                for (int dx = -4; dx <= 4; dx++)
                {
                    int dist = Math.Max(Math.Abs(dx), Math.Abs(dy));
                    int xx = x + dx, yy = y + dy;
                    if (xx >= 0 && xx < Size && yy >= 0 && yy < Size) Set(xx, yy, dist != 2 && dist != 4);
                }
        }

        void DrawAlignment(int x, int y)
        {
            for (int dy = -2; dy <= 2; dy++)
                for (int dx = -2; dx <= 2; dx++)
                    Set(x + dx, y + dy, Math.Max(Math.Abs(dx), Math.Abs(dy)) != 1);
        }

        byte[] AddEccAndInterleave(byte[] data)
        {
            int numBlocks = EccBlocks[(int)Ecc, Version];
            int eccLen = EccPerBlock[(int)Ecc, Version];
            int raw = RawDataModules(Version) / 8;
            int numShort = numBlocks - raw % numBlocks;
            int shortLen = raw / numBlocks;
            byte[] div = RsDivisor(eccLen);
            var blocks = new List<byte[]>();
            for (int i = 0, k = 0; i < numBlocks; i++)
            {
                int datLen = shortLen - eccLen + (i < numShort ? 0 : 1);
                var dat = new byte[datLen];
                Array.Copy(data, k, dat, 0, datLen);
                k += datLen;
                byte[] ecc = RsRemainder(dat, div);
                var block = new byte[shortLen + 1];
                Array.Copy(dat, 0, block, 0, datLen);
                // A short block has a placeholder byte before its ECC.
                Array.Copy(ecc, 0, block, block.Length - eccLen, eccLen);
                blocks.Add(block);
            }
            var result = new List<byte>();
            for (int i = 0; i < shortLen + 1; i++)
                for (int j = 0; j < blocks.Count; j++)
                    if (i != shortLen - eccLen || j >= numShort) result.Add(blocks[j][i]);
            return result.ToArray();
        }

        void DrawCodewords(byte[] data)
        {
            int i = 0;
            for (int right = Size - 1; right >= 1; right -= 2)
            {
                if (right == 6) right = 5;
                for (int vert = 0; vert < Size; vert++)
                    for (int j = 0; j < 2; j++)
                    {
                        int x = right - j;
                        bool upward = ((right + 1) & 2) == 0;
                        int y = upward ? Size - 1 - vert : vert;
                        if (!isFunction[y, x] && i < data.Length * 8)
                        {
                            modules[y, x] = Bit(data[i >> 3], 7 - (i & 7));
                            i++;
                        }
                    }
            }
        }

        void ApplyMask(int mask)
        {
            for (int y = 0; y < Size; y++)
                for (int x = 0; x < Size; x++)
                {
                    bool invert;
                    switch (mask)
                    {
                        case 0: invert = (x + y) % 2 == 0; break;
                        case 1: invert = y % 2 == 0; break;
                        case 2: invert = x % 3 == 0; break;
                        case 3: invert = (x + y) % 3 == 0; break;
                        case 4: invert = (x / 3 + y / 2) % 2 == 0; break;
                        case 5: invert = x * y % 2 + x * y % 3 == 0; break;
                        case 6: invert = (x * y % 2 + x * y % 3) % 2 == 0; break;
                        case 7: invert = ((x + y) % 2 + x * y % 3) % 2 == 0; break;
                        default: throw new ArgumentException("mask");
                    }
                    modules[y, x] ^= invert & !isFunction[y, x];
                }
        }

        int Penalty()
        {
            int result = 0;
            for (int pass = 0; pass < 2; pass++)
            {
                for (int a = 0; a < Size; a++)
                {
                    bool runColor = false;
                    int run = 0;
                    var history = new int[7];
                    for (int b = 0; b < Size; b++)
                    {
                        bool m = pass == 0 ? modules[a, b] : modules[b, a];
                        if (m == runColor)
                        {
                            run++;
                            if (run == 5) result += 3;
                            else if (run > 5) result++;
                        }
                        else
                        {
                            AddHistory(run, history);
                            if (!runColor) result += CountPatterns(history) * 40;
                            runColor = m;
                            run = 1;
                        }
                    }
                    result += TerminateAndCount(runColor, run, history) * 40;
                }
            }
            for (int y = 0; y < Size - 1; y++)
                for (int x = 0; x < Size - 1; x++)
                {
                    bool c = modules[y, x];
                    if (c == modules[y, x + 1] && c == modules[y + 1, x] && c == modules[y + 1, x + 1]) result += 3;
                }
            int dark = 0;
            foreach (bool m in modules) if (m) dark++;
            int total = Size * Size;
            int k = (Math.Abs(dark * 20 - total * 10) + total - 1) / total - 1;
            result += k * 10;
            return result;
        }

        int CountPatterns(int[] h)
        {
            int n = h[1];
            bool core = n > 0 && h[2] == n && h[3] == n * 3 && h[4] == n && h[5] == n;
            return (core && h[0] >= n * 4 && h[6] >= n ? 1 : 0) + (core && h[6] >= n * 4 && h[0] >= n ? 1 : 0);
        }

        int TerminateAndCount(bool runColor, int run, int[] h)
        {
            if (runColor)
            {
                AddHistory(run, h);
                run = 0;
            }
            run += Size;
            AddHistory(run, h);
            return CountPatterns(h);
        }

        void AddHistory(int run, int[] h)
        {
            if (h[0] == 0) run += Size;
            Array.Copy(h, 0, h, 1, h.Length - 1);
            h[0] = run;
        }

        int[] AlignmentPositions()
        {
            if (Version == 1) return new int[0];
            int n = Version / 7 + 2;
            int step = (Version * 8 + n * 3 + 5) / (n * 4 - 4) * 2;
            var result = new int[n];
            result[0] = 6;
            for (int i = n - 1, pos = Size - 7; i >= 1; i--, pos -= step) result[i] = pos;
            return result;
        }

        static int RawDataModules(int ver)
        {
            int result = (16 * ver + 128) * ver + 64;
            if (ver >= 2)
            {
                int n = ver / 7 + 2;
                result -= (25 * n - 10) * n - 55;
                if (ver >= 7) result -= 36;
            }
            return result;
        }

        static int DataCodewords(int ver, QrEcc ecc)
        {
            return RawDataModules(ver) / 8 - EccPerBlock[(int)ecc, ver] * EccBlocks[(int)ecc, ver];
        }

        static byte[] RsDivisor(int degree)
        {
            var result = new byte[degree];
            result[degree - 1] = 1;
            int root = 1;
            for (int i = 0; i < degree; i++)
            {
                for (int j = 0; j < degree; j++)
                {
                    result[j] = (byte)RsMultiply(result[j], root);
                    if (j + 1 < degree) result[j] ^= result[j + 1];
                }
                root = RsMultiply(root, 0x02);
            }
            return result;
        }

        static byte[] RsRemainder(byte[] data, byte[] divisor)
        {
            var result = new byte[divisor.Length];
            foreach (byte b in data)
            {
                int factor = b ^ result[0];
                Array.Copy(result, 1, result, 0, result.Length - 1);
                result[result.Length - 1] = 0;
                for (int i = 0; i < result.Length; i++) result[i] ^= (byte)RsMultiply(divisor[i], factor);
            }
            return result;
        }

        static int RsMultiply(int x, int y)
        {
            int z = 0;
            for (int i = 7; i >= 0; i--)
            {
                z = (z << 1) ^ ((z >> 7) * 0x11D);
                z ^= ((y >> i) & 1) * x;
            }
            return z;
        }

        static bool Bit(int x, int i) { return ((x >> i) & 1) != 0; }

        // One line per row, '#' dark and '.' light: what the tests compare.
        public string Text()
        {
            var sb = new StringBuilder();
            for (int y = 0; y < Size; y++)
            {
                for (int x = 0; x < Size; x++) sb.Append(modules[y, x] ? '#' : '.');
                sb.Append('\n');
            }
            return sb.ToString();
        }
    }
}
