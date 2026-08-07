package io.github.robintra.perfsentinel.service

import com.intellij.openapi.components.service
import com.intellij.openapi.project.Project
import com.intellij.openapi.startup.ProjectActivity
import io.github.robintra.perfsentinel.editor.FindingHighlighter

class PerfSentinelStartupActivity : ProjectActivity {
    override suspend fun execute(project: Project) {
        project.service<FindingHighlighter>()
        project.service<PerfSentinelProjectService>().refresh()
    }
}
