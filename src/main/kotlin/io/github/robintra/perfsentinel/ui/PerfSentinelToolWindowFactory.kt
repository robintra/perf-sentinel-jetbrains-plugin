package io.github.robintra.perfsentinel.ui

import com.intellij.openapi.Disposable
import com.intellij.openapi.components.service
import com.intellij.openapi.options.ShowSettingsUtil
import com.intellij.openapi.project.Project
import com.intellij.openapi.wm.ToolWindow
import com.intellij.openapi.wm.ToolWindowFactory
import com.intellij.ui.components.JBLabel
import com.intellij.ui.components.JBScrollPane
import com.intellij.ui.components.JBTextArea
import com.intellij.ui.content.ContentFactory
import com.intellij.ui.table.JBTable
import com.intellij.util.ui.JBUI
import io.github.robintra.perfsentinel.PerfSentinelBundle
import io.github.robintra.perfsentinel.core.FindingResponse
import io.github.robintra.perfsentinel.core.RefreshState
import io.github.robintra.perfsentinel.service.FINDINGS_TOPIC
import io.github.robintra.perfsentinel.service.FindingsListener
import io.github.robintra.perfsentinel.service.PerfSentinelProjectService
import io.github.robintra.perfsentinel.settings.PerfSentinelConfigurable
import java.awt.BorderLayout
import java.awt.FlowLayout
import java.awt.event.ActionEvent
import java.awt.event.KeyEvent
import java.awt.event.MouseAdapter
import java.awt.event.MouseEvent
import java.time.Duration
import java.time.Instant
import java.time.ZoneId
import java.time.format.DateTimeFormatter
import javax.swing.AbstractAction
import javax.swing.JButton
import javax.swing.JComponent
import javax.swing.JPanel
import javax.swing.JSplitPane
import javax.swing.KeyStroke
import javax.swing.ListSelectionModel
import javax.swing.table.AbstractTableModel

class PerfSentinelToolWindowFactory : ToolWindowFactory {
    override fun createToolWindowContent(project: Project, toolWindow: ToolWindow) {
        val panel = PerfSentinelPanel(project)
        val content = ContentFactory.getInstance().createContent(panel, "", false)
        content.setDisposer(panel)
        toolWindow.contentManager.addContent(content)
    }
}

private class PerfSentinelPanel(private val project: Project) : JPanel(BorderLayout()), Disposable {
    private val service = project.service<PerfSentinelProjectService>()
    private val status = JBLabel()
    private val model = FindingsTableModel()
    private val table = JBTable(model)
    private val details = JBTextArea()
    private var state = service.state

    init {
        border = JBUI.Borders.empty(6)
        add(createToolbar(), BorderLayout.NORTH)
        add(createBody(), BorderLayout.CENTER)
        project.messageBus.connect(this).subscribe(FINDINGS_TOPIC, FindingsListener(::render))
        render(state)
    }

    private fun createToolbar(): JComponent = JPanel(FlowLayout(FlowLayout.LEFT, 6, 0)).apply {
        val refresh = JButton(PerfSentinelBundle.message("action.refresh")).apply {
            toolTipText = PerfSentinelBundle.message("action.refresh.description")
            addActionListener { service.refresh() }
        }
        val settings = JButton(PerfSentinelBundle.message("action.settings")).apply {
            addActionListener {
                ShowSettingsUtil.getInstance().showSettingsDialog(project, PerfSentinelConfigurable::class.java)
            }
        }
        add(refresh)
        add(settings)
        add(status)
    }

    private fun createBody(): JComponent {
        table.apply {
            selectionModel.selectionMode = ListSelectionModel.SINGLE_SELECTION
            autoCreateRowSorter = true
            emptyText.text = PerfSentinelBundle.message("findings.empty")
            accessibleContext.accessibleName = PerfSentinelBundle.message("findings.table")
            selectionModel.addListSelectionListener {
                if (!it.valueIsAdjusting) showSelectedDetails()
            }
            addMouseListener(object : MouseAdapter() {
                override fun mouseClicked(event: MouseEvent) {
                    if (event.clickCount == 2) navigateSelected()
                }
            })
            inputMap.put(KeyStroke.getKeyStroke(KeyEvent.VK_ENTER, 0), "perfSentinel.navigate")
            actionMap.put("perfSentinel.navigate", object : AbstractAction() {
                override fun actionPerformed(event: ActionEvent) = navigateSelected()
            })
        }
        details.apply {
            isEditable = false
            lineWrap = true
            wrapStyleWord = true
            border = JBUI.Borders.empty(6)
            accessibleContext.accessibleName = PerfSentinelBundle.message("findings.details")
        }
        return JSplitPane(JSplitPane.VERTICAL_SPLIT, JBScrollPane(table), JBScrollPane(details)).apply {
            resizeWeight = 0.68
            isOneTouchExpandable = true
        }
    }

    private fun render(newState: RefreshState) {
        state = newState
        model.rows = newState.findings.sortedWith(
            compareByDescending<FindingResponse> { confidenceWeight(it.finding.confidence) }
                .thenByDescending { it.finding.lastTimestamp },
        )
        status.text = statusText(newState)
        status.toolTipText = newState.endpoints
            .mapNotNull { snapshot -> snapshot.error?.let { "${snapshot.endpoint}: $it" } }
            .joinToString("\n")
            .ifEmpty { null }
        if (model.rowCount == 0) details.text = status.toolTipText.orEmpty()
    }

    private fun statusText(current: RefreshState): String {
        if (current.refreshing) return PerfSentinelBundle.message("status.refreshing")
        if (current.endpoints.isEmpty()) return PerfSentinelBundle.message("status.not.refreshed")
        val failures = current.endpoints.count { it.error != null }
        val latest = current.endpoints.mapNotNull { it.lastSuccessAtMillis }.maxOrNull()
        val summary = if (failures == 0) {
            PerfSentinelBundle.message("status.connected", current.findings.size)
        } else {
            PerfSentinelBundle.message("status.partial", current.findings.size, failures)
        }
        return latest?.let { "$summary · ${formatInstant(it)}" } ?: summary
    }

    private fun showSelectedDetails() {
        details.text = selectedFinding()?.let(::detailsText).orEmpty()
        details.caretPosition = 0
    }

    private fun navigateSelected() {
        selectedFinding()?.let { service.navigate(it.finding) }
    }

    private fun selectedFinding(): FindingResponse? {
        val selected = table.selectedRow
        if (selected < 0) return null
        return model.rowAt(table.convertRowIndexToModel(selected))
    }

    private fun detailsText(response: FindingResponse): String {
        val finding = response.finding
        val acknowledgement = response.acknowledgedBy?.let {
            "${PerfSentinelBundle.message("details.acknowledged")}: ${it.by ?: it.source}${it.reason?.let { reason -> " — $reason" }.orEmpty()}\n"
        }.orEmpty()
        return buildString {
            appendLine("${PerfSentinelBundle.message("details.suggestion")}: ${finding.suggestion}")
            appendLine("${PerfSentinelBundle.message("details.pattern")}: ${finding.pattern.template}")
            appendLine("${PerfSentinelBundle.message("details.first.seen")}: ${finding.firstTimestamp}")
            appendLine("${PerfSentinelBundle.message("details.last.seen")}: ${finding.lastTimestamp}")
            appendLine("${PerfSentinelBundle.message("details.signature")}: ${finding.signature}")
            append(acknowledgement)
            state.endpoints.firstOrNull { it.endpoint == response.source }?.error?.let { error ->
                appendLine("${PerfSentinelBundle.message("details.stale")}: $error")
            }
        }.trim()
    }

    override fun dispose() = Unit
}

private class FindingsTableModel : AbstractTableModel() {
    var rows: List<FindingResponse> = emptyList()
        set(value) {
            field = value
            fireTableDataChanged()
        }

    private val columns = listOf(
        "column.confidence",
        "column.severity",
        "column.type",
        "column.location",
        "column.service",
        "column.age",
        "column.source",
        "column.seen",
    )

    override fun getRowCount(): Int = rows.size
    override fun getColumnCount(): Int = columns.size
    override fun getColumnName(column: Int): String = PerfSentinelBundle.message(columns[column])

    override fun getValueAt(rowIndex: Int, columnIndex: Int): Any {
        val response = rows[rowIndex]
        val finding = response.finding
        return when (columnIndex) {
            0 -> finding.confidence
            1 -> finding.severity
            2 -> finding.type
            3 -> locationText(finding.codeLocation)
            4 -> finding.service
            5 -> ageText(finding.lastTimestamp)
            6 -> response.source
            else -> response.seenCount
        }
    }

    fun rowAt(index: Int): FindingResponse = rows[index]
}

private fun locationText(location: io.github.robintra.perfsentinel.core.CodeLocation?): String {
    if (location == null) return "—"
    val symbol = listOfNotNull(location.namespace, location.function).joinToString(".")
    val file = location.filepath?.let { path ->
        location.lineNumber?.let { "$path:$it" } ?: path
    }.orEmpty()
    return listOf(symbol, file).filter(String::isNotEmpty).joinToString(" · ").ifEmpty { "—" }
}

private fun ageText(timestamp: String): String = try {
    val seconds = Duration.between(Instant.parse(timestamp), Instant.now()).seconds.coerceAtLeast(0)
    when {
        seconds < 60 -> PerfSentinelBundle.message("age.seconds", seconds)
        seconds < 3_600 -> PerfSentinelBundle.message("age.minutes", seconds / 60)
        seconds < 86_400 -> PerfSentinelBundle.message("age.hours", seconds / 3_600)
        else -> PerfSentinelBundle.message("age.days", seconds / 86_400)
    }
} catch (_: RuntimeException) {
    timestamp
}

private fun confidenceWeight(confidence: String): Int = when (confidence) {
    "daemon_production" -> 3
    "daemon_staging" -> 2
    else -> 1
}

private fun formatInstant(epochMillis: Long): String = DateTimeFormatter.ofPattern("yyyy-MM-dd HH:mm:ss")
    .withZone(ZoneId.systemDefault())
    .format(Instant.ofEpochMilli(epochMillis))
