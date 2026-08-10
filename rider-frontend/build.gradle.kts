import org.jetbrains.intellij.platform.gradle.Constants
import org.jetbrains.intellij.platform.gradle.TestFrameworkType
import org.jetbrains.intellij.platform.gradle.extensions.intellijPlatform
import org.jetbrains.kotlin.gradle.dsl.JvmTarget

plugins {
    id("org.jetbrains.kotlin.jvm")
    id("org.jetbrains.intellij.platform.module")
}

dependencies {
    compileOnly(project(":"))
    testImplementation(libs.junit)

    intellijPlatform {
        rider("2025.3.5") { useInstaller = false }
        testFramework(TestFrameworkType.Platform)
    }
}

sourceSets.main {
    kotlin.srcDir(layout.buildDirectory.dir("generated/rd/kotlin"))
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

val riderModel by configurations.creating {
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
