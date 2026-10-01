package com.domovoi.app.testing

import java.io.DataInputStream
import java.io.File
import java.net.JarURLConnection
import java.util.jar.JarFile

/**
 * Reads the app's own compiled classes to answer one question: which
 * methods call a given method. For a Compose screen that cannot be composed
 * on the JVM (it opens a dialog window), "who collects this state" decides
 * what recomposes when it changes — a state read recomposes the composable
 * that read it, and nothing else.
 *
 * A plain class-file reader (the JVM spec's format, chapter 4): the constant
 * pool, then each method's Code attribute, decoded instruction by
 * instruction so an operand is never mistaken for an invoke.
 */
internal object Bytecode {

    data class Call(val className: String, val method: String)

    /**
     * Every method, in the compiled classes of the Kotlin file whose facade
     * is [fileFacade] (`FooKt` and its nested lambda classes `FooKt$…`),
     * that invokes [owner].[name] for any name in [names]. [owner] is in
     * internal form (`com/domovoi/app/player/PlayerController`).
     */
    fun callers(fileFacade: String, owner: String, names: Set<String>): List<Call> {
        val classes = classFilesOf(fileFacade)
        check(classes.isNotEmpty()) { "no compiled classes found for $fileFacade" }
        return classes.flatMap { (name, bytes) -> callsIn(name, bytes, owner, names) }
    }

    private fun classFilesOf(fileFacade: String): List<Pair<String, ByteArray>> {
        val path = fileFacade.replace('.', '/')
        val simple = path.substringAfterLast('/')
        val url = Bytecode::class.java.classLoader!!.getResource("$path.class")
            ?: error("$fileFacade is not on the test classpath")
        fun wanted(entry: String) = entry == "$simple.class" ||
            (entry.startsWith("$simple\$") && entry.endsWith(".class"))
        return when (url.protocol) {
            "file" -> File(url.toURI()).parentFile?.listFiles().orEmpty()
                .filter { wanted(it.name) }
                .map { it.name.removeSuffix(".class") to it.readBytes() }
            "jar" -> {
                val jar: JarFile = (url.openConnection() as JarURLConnection).jarFile
                val dir = path.substringBeforeLast('/') + "/"
                jar.entries().toList()
                    .filter { it.name.startsWith(dir) && wanted(it.name.removePrefix(dir)) && !it.name.removePrefix(dir).contains('/') }
                    .map { e -> e.name.substringAfterLast('/').removeSuffix(".class") to jar.getInputStream(e).use { it.readBytes() } }
            }
            else -> error("cannot list classes at $url")
        }
    }

    private fun callsIn(className: String, bytes: ByteArray, owner: String, names: Set<String>): List<Call> {
        val input = DataInputStream(bytes.inputStream())
        check(input.readInt() == 0xCAFEBABE.toInt()) { "$className is not a class file" }
        input.readUnsignedShort(); input.readUnsignedShort()           // version
        val count = input.readUnsignedShort()
        val utf8 = arrayOfNulls<String>(count)
        val refs = IntArray(count * 2)                                  // a, b of two-index entries
        val tags = IntArray(count)
        var i = 1
        while (i < count) {
            val tag = input.readUnsignedByte()
            tags[i] = tag
            when (tag) {
                1 -> utf8[i] = input.readUTF()
                3, 4 -> input.readInt()
                5, 6 -> { input.readLong(); i++ }
                7, 8, 16, 19, 20 -> refs[2 * i] = input.readUnsignedShort()
                9, 10, 11, 12, 17, 18 -> {
                    refs[2 * i] = input.readUnsignedShort(); refs[2 * i + 1] = input.readUnsignedShort()
                }
                15 -> { input.readUnsignedByte(); input.readUnsignedShort() }
                else -> error("$className: unknown constant pool tag $tag")
            }
            i++
        }
        fun classNameAt(index: Int) = utf8[refs[2 * index]]
        fun methodAt(index: Int): Pair<String?, String?>? {
            if (tags[index] != 10 && tags[index] != 11) return null
            val nameAndType = refs[2 * index + 1]
            return classNameAt(refs[2 * index]) to utf8[refs[2 * nameAndType]]
        }

        input.readUnsignedShort(); input.readUnsignedShort(); input.readUnsignedShort()
        repeat(input.readUnsignedShort()) { input.readUnsignedShort() }  // interfaces
        repeat(input.readUnsignedShort()) {                                // fields
            input.readUnsignedShort(); input.readUnsignedShort(); input.readUnsignedShort()
            repeat(input.readUnsignedShort()) {
                input.readUnsignedShort(); input.skipFully(input.readInt())
            }
        }
        val calls = mutableListOf<Call>()
        repeat(input.readUnsignedShort()) {                                // methods
            input.readUnsignedShort()
            val methodName = utf8[input.readUnsignedShort()]!!
            input.readUnsignedShort()
            repeat(input.readUnsignedShort()) {
                val attribute = utf8[input.readUnsignedShort()]
                val length = input.readInt()
                if (attribute != "Code") {
                    input.skipFully(length)
                    return@repeat
                }
                val body = ByteArray(length).also { input.readFully(it) }
                val code = DataInputStream(body.inputStream()).run {
                    readUnsignedShort(); readUnsignedShort()
                    ByteArray(readInt()).also { readFully(it) }
                }
                forEachInvoke(code) { index ->
                    val (cls, name) = methodAt(index) ?: return@forEachInvoke
                    if (cls == owner && name in names) calls += Call(className, methodName)
                }
            }
        }
        return calls
    }

    private fun DataInputStream.skipFully(n: Int) = check(skipBytes(n) == n) { "truncated class file" }

    /** The constant-pool index of every invokevirtual / invokespecial /
     *  invokestatic / invokeinterface in [code]. */
    private fun forEachInvoke(code: ByteArray, onInvoke: (Int) -> Unit) {
        fun u1(at: Int) = code[at].toInt() and 0xFF
        fun u2(at: Int) = (u1(at) shl 8) or u1(at + 1)
        fun s4(at: Int) = (u1(at) shl 24) or (u1(at + 1) shl 16) or (u1(at + 2) shl 8) or u1(at + 3)
        var pc = 0
        while (pc < code.size) {
            val op = u1(pc)
            pc += when (op) {
                in 0xB6..0xB9 -> { onInvoke(u2(pc + 1)); if (op == 0xB9) 5 else 3 }
                0x10, 0x12, 0xA9, 0xBC, in 0x15..0x19, in 0x36..0x3A -> 2
                0x11, 0x13, 0x14, 0x84, in 0x99..0xA8, in 0xB2..0xB5, 0xBB, 0xBD, 0xC0, 0xC1, 0xC6, 0xC7 -> 3
                0xC5 -> 4
                0xBA, 0xC8, 0xC9 -> 5
                0xC4 -> if (u1(pc + 1) == 0x84) 6 else 4
                0xAA -> {
                    val at = (pc + 4) and 3.inv()
                    val low = s4(at + 4)
                    val high = s4(at + 8)
                    at - pc + 12 + (high - low + 1) * 4
                }
                0xAB -> {
                    val at = (pc + 4) and 3.inv()
                    at - pc + 8 + s4(at + 4) * 8
                }
                else -> 1
            }
        }
    }
}
