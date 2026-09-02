import org.jetbrains.intellij.platform.gradle.Constants
import org.jetbrains.intellij.platform.gradle.TestFrameworkType
import org.jetbrains.kotlin.gradle.dsl.JvmTarget

plugins {
    id("idea")
    id("org.jetbrains.kotlin.jvm")
    alias(libs.plugins.kover)
    id("org.jetbrains.intellij.platform.module")
}

dependencies {
    compileOnly(project(":"))
    testImplementation(project(":"))
    testImplementation(platform(libs.jackson.bom))
    testImplementation(libs.junit)

    intellijPlatform {
        rider("2025.3.5") { useInstaller = false }
        testFramework(TestFrameworkType.Platform)
    }
}

val generatedRdKotlin = layout.buildDirectory.dir("generated/rd/kotlin")

sourceSets.main {
    kotlin.srcDir(generatedRdKotlin)
}

idea {
    module {
        generatedSourceDirs.add(generatedRdKotlin.get().asFile)
    }
}

tasks.compileKotlin {
    dependsOn(":protocol:rdgen")
}

tasks.jar {
    archiveFileName = "perf-sentinel-rider-frontend.jar"
}

kotlin {
    jvmToolchain(21)
    compilerOptions.jvmTarget = JvmTarget.JVM_21
}

val riderModel = configurations.create("riderModel") {
    isCanBeConsumed = true
    isCanBeResolved = false
}

artifacts {
    add(riderModel.name, provider {
        intellijPlatform.platformPath.resolve("lib/rd/rider-model.jar").toFile()
    }) {
        builtBy(Constants.Tasks.INITIALIZE_INTELLIJ_PLATFORM_PLUGIN)
    }
}
