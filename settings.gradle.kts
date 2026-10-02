@file:Suppress("UnstableApiUsage")

import org.jetbrains.intellij.platform.gradle.extensions.intellijPlatform

rootProject.name = "perf-sentinel-jetbrains-plugin"

pluginManagement {
    repositories {
        gradlePluginPortal()
        mavenCentral()
    }
    resolutionStrategy {
        eachPlugin {
            if (requested.id.id == "com.jetbrains.rdgen") {
                useModule("com.jetbrains.rd:rd-gen:${requested.version}")
            }
        }
    }
    plugins {
        id("org.jetbrains.kotlin.jvm") version "2.4.20"
        id("org.jetbrains.intellij.platform.module") version "2.19.0"
        id("org.jetbrains.changelog") version "2.5.0"
        id("org.jetbrains.qodana") version "2026.2.2"
    }
}

include(":protocol", ":rider-frontend")

// The IntelliJ Platform plugin brings Jackson and jsoup builds with published advisories onto the
// classpath every build script shares. It never ships, but it still runs on patched versions.
buildscript {
    dependencies {
        classpath(platform("com.fasterxml.jackson:jackson-bom:2.22.3"))
        constraints {
            classpath("org.jsoup:jsoup:1.23.1")
        }
    }
}

plugins {
    id("org.gradle.toolchains.foojay-resolver-convention") version "1.0.0"
    id("org.jetbrains.intellij.platform.settings") version "2.19.0"
}

dependencyResolutionManagement {
    // Configure all projects' repositories
    repositories {
        mavenCentral()

        // IntelliJ Platform Gradle Plugin Repositories Extension - read more: https://plugins.jetbrains.com/docs/intellij/tools-intellij-platform-gradle-plugin-repositories-extension.html
        intellijPlatform {
            defaultRepositories()
        }
    }
}
