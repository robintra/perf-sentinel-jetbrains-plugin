import org.jetbrains.intellij.platform.gradle.TestFrameworkType
import org.jetbrains.intellij.platform.gradle.IntelliJPlatformType
import org.jetbrains.intellij.platform.gradle.extensions.IntelliJPlatformExtension
import org.jetbrains.intellij.platform.gradle.tasks.BuildPluginTask
import org.jetbrains.intellij.platform.gradle.tasks.PrepareSandboxTask
import org.jetbrains.intellij.platform.gradle.tasks.PublishPluginTask
import org.jetbrains.intellij.platform.gradle.tasks.SignPluginTask
import org.jetbrains.intellij.platform.gradle.tasks.VerifyPluginSignatureTask
import org.gradle.api.tasks.bundling.AbstractArchiveTask
import org.jetbrains.kotlin.gradle.dsl.JvmTarget
import org.jetbrains.kotlin.gradle.dsl.JvmDefaultMode

plugins {
    id("idea")
    id("org.jetbrains.kotlin.jvm")
    alias(libs.plugins.kover)
    id("org.jetbrains.changelog")
    id("org.jetbrains.qodana")
    id("org.jetbrains.intellij.platform")
}

allprojects {
    dependencyLocking {
        lockAllConfigurations()
    }
    tasks.withType<AbstractArchiveTask>().configureEach {
        isPreserveFileTimestamps = false
        isReproducibleFileOrder = true
        dirPermissions { unix("755") }
        filePermissions { unix("644") }
    }
}

val ideaTestRuntime = providers.provider {
    extensions.getByType<IntelliJPlatformExtension>().platformPath.resolve("lib/idea_rt.jar").toFile()
}
val pluginVerifierTarget = providers.gradleProperty("pluginVerifierTarget").orNull
val pluginVerifierTargets = setOf(
    "idea-253", "idea-262", "rider-253", "rider-262",
    "python-253", "python-262", "php-253", "php-262",
    "rust-253", "rust-262", "ruby-253", "ruby-262",
    "web-253", "web-262", "go-253", "go-262",
)
require(pluginVerifierTarget == null || pluginVerifierTarget in pluginVerifierTargets) {
    "unknown pluginVerifierTarget: $pluginVerifierTarget"
}
fun verifyTarget(name: String) = pluginVerifierTarget == null || pluginVerifierTarget == name

if (pluginVerifierTarget != null) {
    configurations.named("intellijPluginVerifierIdesDependency") {
        // A matrix runner resolves one exact product. Artifact integrity remains protected by strict verification metadata.
        resolutionStrategy.deactivateDependencyLocking()
    }
}
// IntelliJ Platform Gradle Plugin documentation: https://plugins.jetbrains.com/docs/intellij/tools-intellij-platform-gradle-plugin.html
dependencies {
    compileOnly(libs.gson)
    testImplementation(platform(libs.jackson.bom))
    testImplementation(libs.junit)
    kover(project(":rider-frontend"))

    // IntelliJ Platform Gradle Plugin Dependencies Extension - read more: https://plugins.jetbrains.com/docs/intellij/tools-intellij-platform-gradle-plugin-dependencies-extension.html
    intellijPlatform {
        intellijIdea("2025.3.6.1")
        testFramework(TestFrameworkType.Platform)
        testFramework(TestFrameworkType.Plugin.Java)
        bundledPlugin("com.intellij.java")
        bundledPlugin("org.jetbrains.kotlin")

    }
}

kover {
    reports {
        filters {
            excludes {
                classes("io.github.robintra.perfsentinel.rider.model.*")
            }
        }
    }
}

idea {
    module {
        excludeDirs.addAll(listOf(file(".superpowers"), file("graphify-out")))
    }
}

val runRider = intellijPlatformTesting.runIde.register("runRider") {
    type = IntelliJPlatformType.Rider
    version = "2025.3.5"
    useInstaller = false
    splitMode = false
}

val runRider262 = intellijPlatformTesting.runIde.register("runRider262") {
    type = IntelliJPlatformType.Rider
    version = "2026.2.0.2"
    useInstaller = false
    splitMode = false
}

val dotnetExecutable = "dotnet"
val riderConfiguration = providers.gradleProperty("riderConfiguration").orElse("Debug")
require(riderConfiguration.get() in setOf("Debug", "Release")) {
    "unknown riderConfiguration: ${riderConfiguration.get()}"
}

val compileRiderBackend = tasks.register<Exec>("compileRiderBackend") {
    description = "Builds the Rider ReSharper backend."
    dependsOn(":protocol:rdgen")
    commandLine(
        dotnetExecutable,
        "build",
        "src/dotnet/PerfSentinel.Rider/PerfSentinel.Rider.csproj",
        "--configuration",
        riderConfiguration.get(),
    )
}

val testRiderBackend = tasks.register<Exec>("testRiderBackend") {
    description = "Runs the Rider ReSharper backend tests on Windows."
    dependsOn(":protocol:rdgen")
    // JetBrains' ReSharper SDK test host is supported on Windows; macOS falls back to Mono and cannot restore fixtures.
    onlyIf { System.getProperty("os.name").startsWith("Windows") }
    commandLine(
        dotnetExecutable,
        "test",
        "src/dotnet/PerfSentinel.Rider.Tests/PerfSentinel.Rider.Tests.csproj",
        "--configuration",
        riderConfiguration.get(),
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
    // Keep the plugin under test listed so its plugin.xml loads.
    // Otherwise, every test sees an empty anchorResolver extension point.
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
    from(layout.buildDirectory.dir("dotnet/bin/PerfSentinel.Rider/${riderConfiguration.get()}")) {
        include("PerfSentinel.Rider.dll", "PerfSentinel.Rider.pdb")
        into("${rootProject.name}/dotnet")
    }
}

tasks.named<BuildPluginTask>("buildPlugin") {
    archiveBaseName.set("perf-sentinel")
}

tasks.named<PublishPluginTask>("publishPlugin") {
    channels.set(providers.gradleProperty("marketplaceChannel").map { listOf(it) })
}

val releaseUnsignedZip = providers.gradleProperty("releaseUnsignedZip")
if (releaseUnsignedZip.isPresent) {
    val signPluginTask = tasks.named<SignPluginTask>("signPlugin") {
        archiveFile.set(releaseUnsignedZip.map { layout.projectDirectory.file(it) })
        signedArchiveFile.set(layout.buildDirectory.file("distributions/perf-sentinel-${project.version}-signed.zip"))
        certificateChain.set(providers.environmentVariable("CERTIFICATE_CHAIN"))
        privateKey.set(providers.environmentVariable("PRIVATE_KEY"))
        password.set(providers.environmentVariable("PRIVATE_KEY_PASSWORD"))
        setDependsOn(emptyList<Any>())
    }
    val verifyPluginSignatureTask = tasks.named<VerifyPluginSignatureTask>("verifyPluginSignature") {
        inputArchiveFile.set(signPluginTask.flatMap { it.signedArchiveFile })
        certificateChain.set(providers.environmentVariable("CERTIFICATE_CHAIN"))
        setDependsOn(listOf(signPluginTask))
    }
    tasks.named<PublishPluginTask>("publishPlugin") {
        archiveFile.set(signPluginTask.flatMap { it.signedArchiveFile })
        token.set(providers.environmentVariable("PUBLISH_TOKEN"))
        setDependsOn(listOf(verifyPluginSignatureTask))
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
            if (verifyTarget("idea-253")) create(IntelliJPlatformType.IntellijIdea, "2025.3.6.1")
            if (verifyTarget("idea-262")) create(IntelliJPlatformType.IntellijIdea, "2026.2.1")
            if (verifyTarget("rider-253")) create(IntelliJPlatformType.Rider, "2025.3.5") { useInstaller = false }
            if (verifyTarget("rider-262")) create(IntelliJPlatformType.Rider, "2026.2.0.2") { useInstaller = false }
            if (verifyTarget("python-253")) create(IntelliJPlatformType.PyCharmProfessional, "2025.3.6.1")
            if (verifyTarget("python-262")) create(IntelliJPlatformType.PyCharm, "2026.2.0.1")
            if (verifyTarget("php-253")) create(IntelliJPlatformType.PhpStorm, "2025.3.6.1")
            if (verifyTarget("php-262")) create(IntelliJPlatformType.PhpStorm, "2026.2.1")
            if (verifyTarget("rust-253")) create(IntelliJPlatformType.RustRover, "2025.3.7")
            if (verifyTarget("rust-262")) create(IntelliJPlatformType.RustRover, "2026.2.1")
            if (verifyTarget("ruby-253")) create(IntelliJPlatformType.RubyMine, "2025.3.6.1")
            if (verifyTarget("ruby-262")) create(IntelliJPlatformType.RubyMine, "2026.2.1")
            if (verifyTarget("web-253")) create(IntelliJPlatformType.WebStorm, "2025.3.6.1")
            if (verifyTarget("web-262")) create(IntelliJPlatformType.WebStorm, "2026.2.1")
            if (verifyTarget("go-253")) create(IntelliJPlatformType.GoLand, "2025.3.5.1")
            if (verifyTarget("go-262")) create(IntelliJPlatformType.GoLand, "2026.2.1")
        }
    }
}
