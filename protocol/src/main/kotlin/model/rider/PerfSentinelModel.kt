package model.rider

import com.jetbrains.rd.generator.nova.*
import com.jetbrains.rd.generator.nova.PredefinedType.*
import com.jetbrains.rd.generator.nova.csharp.CSharp50Generator
import com.jetbrains.rd.generator.nova.kotlin.Kotlin11Generator
import com.jetbrains.rider.model.nova.ide.SolutionModel

// Discovered by the RD generator, not referenced from application code.
@Suppress("unused")
object PerfSentinelModel : Ext(SolutionModel.Solution) {
    private val request = structdef("CSharpSymbolRequest") {
        field("namespace", string)
        field("function", string)
    }

    private val anchor = structdef("SourceAnchor") {
        field("path", string)
        field("offset", int)
    }

    init {
        setting(Kotlin11Generator.Namespace, "io.github.robintra.perfsentinel.rider.model")
        setting(CSharp50Generator.Namespace, "PerfSentinel.Rider.Model")
        call("resolveCSharpSymbol", request, anchor.nullable).async
    }
}
