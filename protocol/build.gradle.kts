import com.jetbrains.rd.generator.gradle.RdGenTask

plugins {
    id("org.jetbrains.kotlin.jvm")
    id("com.jetbrains.rdgen") version libs.versions.rdGen
}

dependencies {
    compileOnly(kotlin("stdlib"))
    implementation(libs.rdGen)
    implementation(project(mapOf("path" to ":rider-frontend", "configuration" to "riderModel")))
}

rdgen {
    verbose = true
    packages = "model.rider"

    generator {
        language = "kotlin"
        transform = "asis"
        root = "com.jetbrains.rider.model.nova.ide.IdeRoot"
        namespace = "com.jetbrains.rider.model"
        directory = rootProject.layout.projectDirectory
            .dir("rider-frontend/build/generated/rd/kotlin")
            .asFile.path
    }

    generator {
        language = "csharp"
        transform = "reversed"
        root = "com.jetbrains.rider.model.nova.ide.IdeRoot"
        namespace = "JetBrains.Rider.Model"
        directory = rootProject.layout.buildDirectory.dir("generated/rd/csharp").get().asFile.path
    }
}

tasks.withType<RdGenTask>().configureEach {
    val classPath = sourceSets["main"].runtimeClasspath
    dependsOn(classPath)
    classpath(classPath)
    notCompatibleWithConfigurationCache("RD generator retains Gradle project state")
    doLast {
        listOf(
            rootProject.layout.projectDirectory.dir("rider-frontend/build/generated/rd/kotlin").asFile,
            rootProject.layout.buildDirectory.dir("generated/rd/csharp").get().asFile,
        ).flatMap { directory ->
            directory.walkTopDown().filter { it.isFile }.toList()
        }.sortedBy { it.invariantSeparatorsPath }.forEach { generatedFile ->
            val original = generatedFile.readText(Charsets.UTF_8)
            val normalized = original.replace("\r\n", "\n").replace('\r', '\n')
            if (normalized != original) {
                generatedFile.writeText(normalized, Charsets.UTF_8)
            }
        }
    }
}
