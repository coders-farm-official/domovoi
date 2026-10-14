package com.domovoi.app.roommode

import ai.onnxruntime.OnnxTensor
import ai.onnxruntime.OrtEnvironment
import ai.onnxruntime.OrtSession
import java.io.File
import java.nio.FloatBuffer
import java.nio.LongBuffer

/**
 * openWakeWord's three networks on ONNX Runtime — the same runtime the
 * phone's voice already ships for all four ABIs (armeabi-v7a included), so
 * room mode adds no native library and loads none of the assistant's.
 * One thread each: a room listens all day, on old phones.
 */
class OrtWakeNets(
    melspectrogram: File,
    embedding: File,
    wakeModel: File,
) : WakeNets {
    private val env = OrtEnvironment.getEnvironment()
    private val mel = open(env, melspectrogram)
    private val emb = open(env, embedding)
    private val wakeSession = open(env, wakeModel)
    private val melInput = mel.inputNames.first()
    private val embInput = emb.inputNames.first()
    private val wakeInput = wakeSession.inputNames.first()

    override val wakeInputFrames: Int = run {
        val info = wakeSession.inputInfo.getValue(wakeInput).info as ai.onnxruntime.TensorInfo
        info.shape.getOrNull(1)?.toInt()?.takeIf { it > 0 } ?: 16
    }

    override fun melspectrogram(samples: FloatArray): Array<FloatArray> {
        OnnxTensor.createTensor(env, FloatBuffer.wrap(samples), longArrayOf(1, samples.size.toLong())).use { input ->
            mel.run(mapOf(melInput to input)).use { result ->
                val out = result[0] as OnnxTensor
                val shape = out.info.shape
                val bins = shape.last().toInt()
                val flat = out.floatBuffer
                val frames = flat.remaining() / bins
                return Array(frames) { f -> FloatArray(bins) { flat.get(f * bins + it) } }
            }
        }
    }

    override fun embedding(window: Array<FloatArray>): FloatArray {
        val rows = window.size
        val bins = window[0].size
        val flat = FloatArray(rows * bins)
        for (r in 0 until rows) System.arraycopy(window[r], 0, flat, r * bins, bins)
        OnnxTensor.createTensor(env, FloatBuffer.wrap(flat), longArrayOf(1, rows.toLong(), bins.toLong(), 1)).use { input ->
            emb.run(mapOf(embInput to input)).use { result ->
                val buf = (result[0] as OnnxTensor).floatBuffer
                return FloatArray(buf.remaining()) { buf.get(it) }
            }
        }
    }

    override fun wake(features: Array<FloatArray>): Float {
        val rows = features.size
        val dim = features[0].size
        val flat = FloatArray(rows * dim)
        for (r in 0 until rows) System.arraycopy(features[r], 0, flat, r * dim, dim)
        OnnxTensor.createTensor(env, FloatBuffer.wrap(flat), longArrayOf(1, rows.toLong(), dim.toLong())).use { input ->
            wakeSession.run(mapOf(wakeInput to input)).use { result ->
                val buf = (result[0] as OnnxTensor).floatBuffer
                return if (buf.remaining() > 0) buf.get(0) else 0f
            }
        }
    }

    override fun close() {
        runCatching { mel.close() }
        runCatching { emb.close() }
        runCatching { wakeSession.close() }
    }

    companion object {
        internal fun open(env: OrtEnvironment, file: File): OrtSession =
            OrtSession.SessionOptions().use { options ->
                options.setIntraOpNumThreads(1)
                options.setInterOpNumThreads(1)
                env.createSession(file.absolutePath, options)
            }
    }
}

/**
 * Silero VAD v4 (MIT; `silero_vad.onnx`, the copy openWakeWord ships and
 * the core pins) on ONNX Runtime: speech probability of one 30 ms frame,
 * carrying the network's state from frame to frame, exactly as
 * openWakeWord's `VAD.predict` runs it (frame / 32767, sr 16000).
 * Not thread-safe; [reset] between captures is optional.
 */
class SileroVad(model: File) : AutoCloseable {
    private val env = OrtEnvironment.getEnvironment()
    private val session = OrtWakeNets.open(env, model)
    private var h = FloatArray(STATE)
    private var c = FloatArray(STATE)

    fun reset() {
        h = FloatArray(STATE)
        c = FloatArray(STATE)
    }

    /** Probability (0..1) that [count] samples of [frame] are speech. */
    fun probability(frame: ShortArray, count: Int = frame.size): Float {
        val x = FloatArray(count) { frame[it] / 32767f }
        val shape = longArrayOf(2, 1, 64)
        OnnxTensor.createTensor(env, FloatBuffer.wrap(x), longArrayOf(1, count.toLong())).use { input ->
            OnnxTensor.createTensor(env, LongBuffer.wrap(longArrayOf(16_000L)), longArrayOf()).use { sr ->
                OnnxTensor.createTensor(env, FloatBuffer.wrap(h), shape).use { hT ->
                    OnnxTensor.createTensor(env, FloatBuffer.wrap(c), shape).use { cT ->
                        session.run(mapOf("input" to input, "sr" to sr, "h" to hT, "c" to cT)).use { result ->
                            val out = (result[0] as OnnxTensor).floatBuffer.get(0)
                            h = (result[1] as OnnxTensor).floatBuffer.let { b -> FloatArray(b.remaining()) { b.get(it) } }
                            c = (result[2] as OnnxTensor).floatBuffer.let { b -> FloatArray(b.remaining()) { b.get(it) } }
                            return out
                        }
                    }
                }
            }
        }
    }

    override fun close() {
        runCatching { session.close() }
    }

    private companion object {
        const val STATE = 2 * 1 * 64
    }
}
