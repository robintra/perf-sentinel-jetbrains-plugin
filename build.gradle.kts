import org.jetbrains.intellij.platform.gradle.TestFrameworkType
import org.jetbrains.intellij.platform.gradle.IntelliJPlatformType
import org.jetbrains.intellij.platform.gradle.extensions.IntelliJPlatformExtension
import org.jetbrains.intellij.platform.gradle.tasks.PrepareSandboxTask
import org.jetbrains.kotlin.gradle.dsl.JvmTarget
import org.jetbrains.kotlin.gradle.dsl.JvmDefaultMode

plugins {
    id("org.jetbrains.kotlin.jvm")
    id("org.jetbrains.changelog")
    id("org.jetbrains.qodana")
    id("org.jetbrains.intellij.platform")
}

val ideaTestRuntime = providers.provider {
    extensions.getByType<IntelliJPlatformExtension>().platformPath.resolve("lib/idea_rt.jar").toFile()
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

val runRider262 by intellijPlatformTesting.runIde.registering {
    type = IntelliJPlatformType.Rider
    version = "2026.2.0.2"
    useInstaller = false
    splitMode = false
}

val dotnetExecutable = file("/usr/local/share/dotnet/dotnet")
    .takeIf { it.isFile }
    ?.absolutePath
    ?: "dotnet"

val compileRiderBackend by tasks.registering(Exec::class) {
    dependsOn(":protocol:rdgen")
    commandLine(
        dotnetExecutable,
        "build",
        "src/dotnet/PerfSentinel.Rider/PerfSentinel.Rider.csproj",
        "--configuration",
        "Debug",
    )
}

val testRiderBackend by tasks.registering(Exec::class) {
    dependsOn(":protocol:rdgen")
    // JetBrains' ReSharper SDK test host is supported on Windows; macOS falls back to Mono and cannot restore fixtures.
    onlyIf { System.getProperty("os.name").startsWith("Windows") }
    commandLine(
        dotnetExecutable,
        "test",
        "src/dotnet/PerfSentinel.Rider.Tests/PerfSentinel.Rider.Tests.csproj",
        "--configuration",
        "Debug",
    )
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

intellijPlatformTesting.testIde.register("testRustRover253") {
    type = IntelliJPlatformType.RustRover
    version = "2025.3.7"
    useInstaller = false
    testFramework(TestFrameworkType.Platform, "253.33813.55")
    task {
        classpath += files(ideaTestRuntime)
        filter {
            includeTestsMatching("*RustAnchorResolverTest")
        }
    }
}

intellijPlatformTesting.testIde.register("testRustRover262") {
    type = IntelliJPlatformType.RustRover
    version = "2026.2.1"
    useInstaller = false
    testFramework(TestFrameworkType.Platform, "262.8665.337")
    task {
        classpath += files(ideaTestRuntime)
        filter {
            includeTestsMatching("*RustAnchorResolverTest")
        }
    }
}

intellijPlatformTesting.testIde.register("testRubyMine253") {
    type = IntelliJPlatformType.RubyMine
    version = "2025.3.6.1"
    useInstaller = false
    testFramework(TestFrameworkType.Platform)
    task {
        classpath += files(ideaTestRuntime)
        filter {
            includeTestsMatching("*RubyAnchorResolverTest")
        }
    }
}

intellijPlatformTesting.testIde.register("testWebStorm253") {
    type = IntelliJPlatformType.WebStorm
    version = "2025.3.6.1"
    useInstaller = false
    testFramework(TestFrameworkType.Platform)
    plugins {
        // The 2025.3 test fixture misidentifies Vue's lib/modules directory as the plugin lib root.
        disablePlugin("org.jetbrains.plugins.vue")
    }
    task {
        classpath += files(ideaTestRuntime)
        filter {
            includeTestsMatching("*JavaScriptAnchorResolverTest")
        }
    }
}

intellijPlatformTesting.testIde.register("testGoLand253") {
    type = IntelliJPlatformType.GoLand
    version = "2025.3.5.1"
    useInstaller = false
    testFramework(TestFrameworkType.Platform)
    task {
        classpath += files(ideaTestRuntime)
        filter {
            includeTestsMatching("*GoAnchorResolverTest")
        }
    }
}

tasks.test {
    // The IDEA 2025.3 fixture cannot initialize Vue when project files are removed during teardown.
    // The plugin under test has to stay listed, or its own plugin.xml never loads and the
    // anchorResolver extension point the production entry reads is empty in every test.
    systemProperty(
        "idea.load.plugins.id",
        "com.intellij.java,org.jetbrains.kotlin,io.github.robintra.perfsentinel",
    )
    filter {
        excludeTestsMatching("io.github.robintra.perfsentinel.python.*")
        excludeTestsMatching("io.github.robintra.perfsentinel.php.*")
        excludeTestsMatching("io.github.robintra.perfsentinel.rust.*")
        excludeTestsMatching("io.github.robintra.perfsentinel.ruby.*")
        excludeTestsMatching("io.github.robintra.perfsentinel.javascript.*")
        excludeTestsMatching("io.github.robintra.perfsentinel.go.*")
    }
}

tasks.check {
    dependsOn(compileRiderBackend, testRiderBackend)
    dependsOn(
        "testPyCharm253",
        "testPhpStorm253",
        "testRustRover253",
        "testRustRover262",
        "testRubyMine253",
        "testWebStorm253",
        "testGoLand253",
    )
}

tasks.withType<PrepareSandboxTask>().configureEach {
    dependsOn(compileRiderBackend, ":rider-frontend:jar")
    from(project(":rider-frontend").layout.buildDirectory.file("libs/perf-sentinel-rider-frontend.jar")) {
        into("${rootProject.name}/lib")
    }
    from(layout.buildDirectory.dir("dotnet/bin/PerfSentinel.Rider/Debug")) {
        include("PerfSentinel.Rider.dll", "PerfSentinel.Rider.pdb")
        into("${rootProject.name}/dotnet")
    }
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
            create(IntelliJPlatformType.Rider, "2025.3.5") { useInstaller = false }
            create(IntelliJPlatformType.Rider, "2026.2.0.2") { useInstaller = false }
            create(IntelliJPlatformType.PyCharmProfessional, "2025.3.6.1")
            create(IntelliJPlatformType.PyCharm, "2026.2.0.1")
            create(IntelliJPlatformType.PhpStorm, "2025.3.6.1")
            create(IntelliJPlatformType.PhpStorm, "2026.2.0.1")
            create(IntelliJPlatformType.RustRover, "2025.3.7")
            create(IntelliJPlatformType.RustRover, "2026.2.1")
            create(IntelliJPlatformType.RubyMine, "2025.3.6.1")
            create(IntelliJPlatformType.RubyMine, "2026.2")
            create(IntelliJPlatformType.WebStorm, "2025.3.6.1")
            create(IntelliJPlatformType.WebStorm, "2026.2.1")
            create(IntelliJPlatformType.GoLand, "2025.3.5.1")
            create(IntelliJPlatformType.GoLand, "2026.2.0.1")
        }
    }
}
