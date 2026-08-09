package io.github.robintra.perfsentinel.php

import com.intellij.openapi.project.Project
import com.intellij.pom.Navigatable
import io.github.robintra.perfsentinel.core.Finding
import io.github.robintra.perfsentinel.navigation.AnchorResolver
import io.github.robintra.perfsentinel.navigation.PlatformSymbolAnchorResolver

class PhpAnchorResolver : AnchorResolver {
    override suspend fun resolve(project: Project, finding: Finding): Navigatable? =
        PlatformSymbolAnchorResolver.resolve(project, finding, setOf("PHP"))
}
