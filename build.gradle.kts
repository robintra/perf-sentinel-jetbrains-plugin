import org.jetbrains.intellij.platform.gradle.TestFrameworkType
import org.jetbrains.intellij.platform.gradle.IntelliJPlatformType
import org.jetbrains.kotlin.gradle.dsl.JvmTarget
import org.jetbrains.kotlin.gradle.dsl.JvmDefaultMode

plugins {
    id("org.jetbrains.kotlin.jvm")
    id("org.jetbrains.changelog")
    id("org.jetbrains.qodana")
    id("org.jetbrains.intellij.platform")
}

// Read more: https://plugins.jetbrains.com/docs/intellij/tools-intellij-platform-gradle-plugin.html
dependencies {
    compileOnly(libs.gson)
    testImplementation(libs.junit)

    // IntelliJ Platform Gradle Plugin Dependencies Extension - read more: https://plugins.jetbrains.com/docs/intellij/tools-intellij-platform-gradle-plugin-dependencies-extension.html
    intellijPlatform {
        intellijIdea("2025.3.6.1")
        testFramework(TestFrameworkType.Platform)
        testFramework(TestFrameworkType.Plugin.Java)
        bundledPlugin("com.intellij.java")
        bundledPlugin("org.jetbrains.kotlin")

        // Add plugin dependencies for compilation here, for example:
        // bundledPlugin("com.intellij.java")
    }
}

val runRider by intellijPlatformTesting.runIde.registering {
    type = IntelliJPlatformType.Rider
    version = "2025.3.5"
    useInstaller = false
    splitMode = false
}

intellijPlatformTesting.testIde.register("testPyCharm253") {
    type = IntelliJPlatformType.PyCharmProfessional
    version = "2025.3.6.1"
    useInstaller = false
    testFramework(TestFrameworkType.Platform)
    task {
        filter {
            includeTestsMatching("*PythonAnchorResolverTest")
        }
    }
}

intellijPlatformTesting.testIde.register("testPhpStorm253") {
    type = IntelliJPlatformType.PhpStorm
    version = "2025.3.6.1"
    useInstaller = false
    testFramework(TestFrameworkType.Platform)
    task {
        filter {
            includeTestsMatching("*PhpAnchorResolverTest")
        }
    }
}

tasks.test {
    filter {
        excludeTestsMatching("io.github.robintra.perfsentinel.python.*")
        excludeTestsMatching("io.github.robintra.perfsentinel.php.*")
    }
}

tasks.check {
    dependsOn("testPyCharm253", "testPhpStorm253")
}

kotlin {
    jvmToolchain(21)
    compilerOptions {
        jvmTarget = JvmTarget.JVM_21
        jvmDefault = JvmDefaultMode.NO_COMPATIBILITY
    }
}

intellijPlatform {
    pluginConfiguration {
        version = project.version.toString()
        ideaVersion {
            sinceBuild = "253"
        }
    }
    pluginVerification {
        ides {
            create(IntelliJPlatformType.IntellijIdea, "2025.3.6.1")
            create(IntelliJPlatformType.IntellijIdea, "2026.2.0.1")
            create(IntelliJPlatformType.Rider, "2025.3.5")
            create(IntelliJPlatformType.Rider, "2026.2.0.2")
            create(IntelliJPlatformType.PyCharmProfessional, "2025.3.6.1")
            create(IntelliJPlatformType.PyCharm, "2026.2.0.1")
            create(IntelliJPlatformType.PhpStorm, "2025.3.6.1")
            create(IntelliJPlatformType.PhpStorm, "2026.2.0.1")
        }
    }
}
