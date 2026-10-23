// Ed25519 signature verification (RFC 8032, section 5.1.7), for the
// Windows update's release signatures (ReleaseSignature.cs). .NET
// Framework 4.8.1 has no Ed25519 and Windows' CNG has no EdDSA, so this is
// the curve arithmetic itself, with System.Numerics.BigInteger: slow next
// to a C library (a few milliseconds per check), and the tray checks one
// signature per file. Verification only: nothing secret goes through it,
// so it needs no constant-time care.
//
// The check is the one OpenSSH's (ref10's) makes: S below the group order,
// A and R points on the curve in their canonical encoding, and the
// encoding of [S]B - [k]A equal to R's bytes, with k = SHA-512(R || A || M)
// mod L. The tests run RFC 8032's own vectors through it.

using System;
using System.Numerics;
using System.Security.Cryptography;

namespace Domovoi.Windows
{
    public static class Ed25519
    {
        public const int PublicKeyBytes = 32;
        public const int SignatureBytes = 64;

        static readonly BigInteger P = BigInteger.Pow(2, 255) - 19;
        static readonly BigInteger L = BigInteger.Pow(2, 252) + BigInteger.Parse("27742317777372353535851937790883648493");
        static readonly BigInteger D = Mod(-121665 * Inverse(121666));
        static readonly BigInteger D2 = Mod(2 * D);
        // A square root of -1 mod p.
        static readonly BigInteger SqrtM1 = BigInteger.ModPow(2, (P - 1) / 4, P);
        static readonly Point Base = BasePoint();

        // Extended homogeneous coordinates: x = X/Z, y = Y/Z, x*y = T/Z.
        struct Point
        {
            public BigInteger X, Y, Z, T;
            public Point(BigInteger x, BigInteger y, BigInteger z, BigInteger t) { X = x; Y = y; Z = z; T = t; }
        }

        static BigInteger Mod(BigInteger a)
        {
            BigInteger r = BigInteger.Remainder(a, P);
            return r.Sign < 0 ? r + P : r;
        }

        static BigInteger Inverse(BigInteger a) { return BigInteger.ModPow(Mod(a), P - 2, P); }

        static Point Identity() { return new Point(BigInteger.Zero, BigInteger.One, BigInteger.One, BigInteger.Zero); }

        static Point BasePoint()
        {
            BigInteger y = Mod(4 * Inverse(5));
            BigInteger x;
            if (!RecoverX(y, 0, out x)) throw new InvalidOperationException("the base point does not decode");
            return new Point(x, y, BigInteger.One, Mod(x * y));
        }

        // RFC 8032 5.1.4.
        static Point Add(Point p, Point q)
        {
            BigInteger a = Mod((p.Y - p.X) * (q.Y - q.X));
            BigInteger b = Mod((p.Y + p.X) * (q.Y + q.X));
            BigInteger c = Mod(p.T * D2 * q.T);
            BigInteger d = Mod(2 * p.Z * q.Z);
            BigInteger e = b - a, f = d - c, g = d + c, h = b + a;
            return new Point(Mod(e * f), Mod(g * h), Mod(f * g), Mod(e * h));
        }

        static Point Negate(Point p) { return new Point(Mod(-p.X), p.Y, p.Z, Mod(-p.T)); }

        static Point Multiply(Point p, BigInteger k)
        {
            Point r = Identity();
            Point addend = p;
            while (k.Sign > 0)
            {
                if (!k.IsEven) r = Add(r, addend);
                addend = Add(addend, addend);
                k >>= 1;
            }
            return r;
        }

        // x from y and the sign bit (RFC 8032 5.1.3); false when y is no
        // point's.
        static bool RecoverX(BigInteger y, int sign, out BigInteger x)
        {
            x = BigInteger.Zero;
            if (y >= P) return false;
            BigInteger yy = Mod(y * y);
            BigInteger u = Mod(yy - 1), v = Mod(D * yy + 1);
            BigInteger v3 = Mod(v * v * v);
            BigInteger cand = Mod(u * v3 * BigInteger.ModPow(Mod(u * v3 * v3 * v), (P - 5) / 8, P));
            BigInteger vxx = Mod(v * cand * cand);
            if (vxx == u) { }
            else if (vxx == Mod(-u)) cand = Mod(cand * SqrtM1);
            else return false;
            if (cand.IsZero && sign == 1) return false;
            if ((int)(cand & 1) != sign) cand = P - cand;
            x = cand;
            return true;
        }

        static bool Decode(byte[] b, int offset, out Point p)
        {
            p = Identity();
            var le = new byte[33];
            Array.Copy(b, offset, le, 0, 32);
            int sign = (le[31] >> 7) & 1;
            le[31] &= 0x7f;
            BigInteger y = new BigInteger(le);
            BigInteger x;
            if (!RecoverX(y, sign, out x)) return false;
            p = new Point(x, y, BigInteger.One, Mod(x * y));
            return true;
        }

        static byte[] Encode(Point p)
        {
            BigInteger zi = Inverse(p.Z);
            BigInteger x = Mod(p.X * zi), y = Mod(p.Y * zi);
            byte[] raw = y.ToByteArray();
            var outb = new byte[32];
            Array.Copy(raw, outb, Math.Min(raw.Length, 32));
            if (!x.IsEven) outb[31] |= 0x80;
            return outb;
        }

        static BigInteger LittleEndian(byte[] b, int offset, int count)
        {
            var le = new byte[count + 1];
            Array.Copy(b, offset, le, 0, count);
            return new BigInteger(le);
        }

        // Whether sig (64 bytes) is publicKey's (32 bytes) signature of message.
        public static bool Verify(byte[] publicKey, byte[] message, byte[] sig)
        {
            if (publicKey == null || publicKey.Length != PublicKeyBytes || sig == null || sig.Length != SignatureBytes || message == null)
                return false;
            Point a, r;
            if (!Decode(publicKey, 0, out a) || !Decode(sig, 0, out r)) return false;
            BigInteger s = LittleEndian(sig, 32, 32);
            if (s >= L) return false;
            byte[] h;
            using (var sha = SHA512.Create())
            {
                sha.TransformBlock(sig, 0, 32, null, 0);
                sha.TransformBlock(publicKey, 0, 32, null, 0);
                sha.TransformFinalBlock(message, 0, message.Length);
                h = sha.Hash;
            }
            BigInteger k = BigInteger.Remainder(LittleEndian(h, 0, 64), L);
            Point check = Add(Multiply(Base, s), Negate(Multiply(a, k)));
            byte[] got = Encode(check);
            int diff = 0;
            for (int i = 0; i < 32; i++) diff |= got[i] ^ sig[i];
            return diff == 0;
        }
    }
}
