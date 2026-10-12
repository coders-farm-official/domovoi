package com.domovoi.app.assistant

import ai.onnxruntime.OnnxTensor
import ai.onnxruntime.OrtEnvironment
import ai.onnxruntime.OrtException
import ai.onnxruntime.OrtSession
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.float
import kotlinx.serialization.json.floatOrNull
import kotlinx.serialization.json.int
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import java.io.File
import java.nio.FloatBuffer
import java.nio.LongBuffer

/** What synthesis needs from a Piper voice's `.onnx.json`. */
data class PiperConfig(
    val sampleRate: Int,
    val noiseScale: Float,
    val lengthScale: Float,
    val noiseW: Float,
    /** The voice's own phoneme id map (Domovoi's three share one). */
    val idMap: Map<String, IntArray>,
) {
    companion object {
        fun parse(json: String): PiperConfig {
            val o = Json.parseToJsonElement(json).jsonObject
            val inference = o["inference"]?.jsonObject
            fun scale(name: String, default: Float) = inference?.get(name)?.jsonPrimitive?.floatOrNull ?: default
            return PiperConfig(
                sampleRate = o.getValue("audio").jsonObject.getValue("sample_rate").jsonPrimitive.int,
                noiseScale = scale("noise_scale", 0.667f),
                lengthScale = scale("length_scale", 1f),
                noiseW = scale("noise_w", 0.8f),
                idMap = o.getValue("phoneme_id_map").jsonObject.mapValues { (_, v) ->
                    v.jsonArray.map { it.jsonPrimitive.int }.toIntArray()
                },
            )
        }
    }
}

/** Phoneme ids -> mono audio. */
interface VoiceModel : AutoCloseable {
    val sampleRate: Int

    /** One sentence's ids -> its audio, peak-normalized to [-1, 1] the way
     *  Piper does. Throws [Stopped] when [cancel] cut it off. */
    fun synthesize(ids: IntArray): FloatArray

    /** Abandons a synthesis in progress (from another thread). */
    fun cancel()

    class Stopped : Exception("synthesis stopped")
}

/**
 * A Piper VITS voice (medium, 22,050 Hz) on ONNX Runtime, run directly:
 * inputs `input` (int64 [1, N] ids), `input_lengths` ([N]) and `scales`
 * (noise, length, noise_w from the voice's config); output float audio.
 * Each sentence is scaled so its loudest sample is full scale, as Piper's
 * own tools do, so the phone sounds like the server.
 */
class PiperVoice(model: File, val config: PiperConfig, threads: Int) : VoiceModel {
    private val env = OrtEnvironment.getEnvironment()
    private val session: OrtSession = OrtSession.SessionOptions().use { options ->
        options.setIntraOpNumThreads(threads)
        options.setInterOpNumThreads(1)
        env.createSession(model.absolutePath, options)
    }
    @Volatile private var running: OrtSession.RunOptions? = null
    @Volatile private var cancelled = false

    override val sampleRate: Int get() = config.sampleRate

    override fun synthesize(ids: IntArray): FloatArray {
        cancelled = false
        val longs = LongArray(ids.size) { ids[it].toLong() }
        val scales = floatArrayOf(config.noiseScale, config.lengthScale, config.noiseW)
        OrtSession.RunOptions().use { options ->
            running = options
            try {
                OnnxTensor.createTensor(env, LongBuffer.wrap(longs), longArrayOf(1, longs.size.toLong())).use { input ->
                    OnnxTensor.createTensor(env, LongBuffer.wrap(longArrayOf(longs.size.toLong())), longArrayOf(1)).use { lengths ->
                        OnnxTensor.createTensor(env, FloatBuffer.wrap(scales), longArrayOf(3)).use { scaleTensor ->
                            if (cancelled) throw VoiceModel.Stopped()
                            val result = try {
                                session.run(mapOf("input" to input, "input_lengths" to lengths, "scales" to scaleTensor), options)
                            } catch (e: OrtException) {
                                if (cancelled) throw VoiceModel.Stopped()
                                throw e
                            }
                            result.use {
                                val buffer = (it[0] as OnnxTensor).floatBuffer
                                val audio = FloatArray(buffer.remaining()).also { a -> buffer.get(a) }
                                return peakNormalize(audio)
                            }
                        }
                    }
                }
            } finally {
                running = null
            }
        }
    }

    override fun cancel() {
        cancelled = true
        runCatching { running?.setTerminate(true) }
    }

    override fun close() {
        cancel()
        session.close()
    }

    companion object {
        /** Piper's scaling: the loudest sample to full scale (no more than
         *  100x for a near-silent sentence). */
        fun peakNormalize(audio: FloatArray): FloatArray {
            var peak = 0f
            for (s in audio) peak = maxOf(peak, kotlin.math.abs(s))
            val gain = 1f / maxOf(0.01f, peak)
            return FloatArray(audio.size) { (audio[it] * gain).coerceIn(-1f, 1f) }
        }

        /** Threads for one synthesis: the phone's big cores, at most four. */
        fun defaultThreads(): Int = Runtime.getRuntime().availableProcessors().coerceIn(1, 4)
    }
}
