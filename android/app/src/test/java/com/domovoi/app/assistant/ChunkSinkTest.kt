package com.domovoi.app.assistant

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Test

/**
 * sherpa-onnx's native TTS calls `invoke([F)Ljava/lang/Integer;` on the
 * callback it is given; a Kotlin 2 lambda lacks that method and the app
 * aborts. ChunkSink must keep it.
 */
class ChunkSinkTest {

    @Test fun theCallbackHasTheMethodTheNativeSideLooksUp() {
        val method = ChunkSink::class.java.getMethod("invoke", FloatArray::class.java)
        assertEquals(java.lang.Integer::class.java, method.returnType)
    }

    @Test fun itCountsFramesAndStopsWhenTold() {
        var calls = 0
        val sink = ChunkSink { calls++ < 1 }
        assertEquals(1, sink(FloatArray(100)))
        assertNotNull(sink.firstChunkNs)
        assertEquals(0, sink(FloatArray(50)))
        assertEquals(150L, sink.frames)
    }
}
