package io.github.robintra.perfsentinel

import com.intellij.DynamicBundle
import org.jetbrains.annotations.PropertyKey

object PerfSentinelBundle : DynamicBundle("messages.PerfSentinelBundle") {
    fun message(
        @PropertyKey(resourceBundle = "messages.PerfSentinelBundle") key: String,
        vararg params: Any,
    ): String = getMessage(key, *params)
}
