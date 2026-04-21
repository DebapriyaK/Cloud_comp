/**
 * Carbon-Aware Code Analyzer — VS Code Extension
 *
 * What it does:
 *   1. Every time you SAVE a Python file, it runs carbon_analyzer.py --json
 *      against that file (no code is executed — pure static analysis).
 *   2. For every detected pattern it adds:
 *        • A green "⚡ green fix" decoration at the END of the first line
 *          of the flagged block.
 *        • A hover tooltip on that block with the full carbon comparison
 *          and quick-fix suggestion.
 *        • A diagnostic (info squiggle) under the flagged line so it also
 *          shows up in the Problems panel.
 *   3. Everything clears and re-runs on the next save.
 */

const vscode  = require('vscode');
const { spawn } = require('child_process');
const path    = require('path');
const os      = require('os');

// ── Decoration type for the inline "⚡ green fix" label ───────────────────
const GREEN_FIX_DECO = vscode.window.createTextEditorDecorationType({
    after: {
        contentText:     '  ⚡ green fix',
        color:           '#4EC94E',   // bright green
        fontStyle:       'italic',
        fontWeight:      'normal',
        margin:          '0 0 0 12px',
    },
    // Subtle green tint on the line itself
    backgroundColor:  'rgba(78, 201, 78, 0.07)',
    isWholeLine:       false,
});

// ── Diagnostic collection (Problems panel) ───────────────────────────────
const DIAG_COLLECTION = vscode.languages.createDiagnosticCollection('carbon-analyzer');

// ── In-memory cache: file URI string -> findings array ───────────────────
const findingsCache = new Map();

// ── Debounce timer (avoid re-running on every keystroke if onChange used) ─
let debounceTimer = null;


// ─────────────────────────────────────────────────────────────────────────
// ACTIVATE
// ─────────────────────────────────────────────────────────────────────────
function activate(context) {
    console.log('Carbon-Aware Analyzer: activated');

    // Run on save (primary trigger — matches the "just finished writing" UX)
    context.subscriptions.push(
        vscode.workspace.onDidSaveTextDocument(doc => {
            if (doc.languageId === 'python') {
                runAnalyzer(doc);
            }
        })
    );

    // Run when a Python file is opened
    context.subscriptions.push(
        vscode.workspace.onDidOpenTextDocument(doc => {
            if (doc.languageId === 'python') {
                runAnalyzer(doc);
            }
        })
    );

    // Re-apply decorations when switching editor tabs (cache is still valid)
    context.subscriptions.push(
        vscode.window.onDidChangeActiveTextEditor(editor => {
            if (editor && editor.document.languageId === 'python') {
                const cached = findingsCache.get(editor.document.uri.toString());
                if (cached) applyDecorations(editor, cached);
            }
        })
    );

    // Analyze any already-open Python files on startup
    vscode.workspace.textDocuments.forEach(doc => {
        if (doc.languageId === 'python') runAnalyzer(doc);
    });

    // Register hover provider for Python files
    context.subscriptions.push(
        vscode.languages.registerHoverProvider(
            { language: 'python' },
            { provideHover }
        )
    );

    // Clean up on deactivate
    context.subscriptions.push(DIAG_COLLECTION);
    context.subscriptions.push(GREEN_FIX_DECO);
}


// ─────────────────────────────────────────────────────────────────────────
// RUN THE PYTHON ANALYZER
// ─────────────────────────────────────────────────────────────────────────
function runAnalyzer(doc) {
    const config      = vscode.workspace.getConfiguration('carbonAnalyzer');
    const pythonPath  = config.get('pythonPath') || 'python';

    // Resolve path to carbon_analyzer.py
    let scriptPath = config.get('analyzerScript') || '';
    if (!scriptPath) {
        // Auto-detect: the script lives one directory above this extension folder
        scriptPath = path.join(__dirname, '..', 'carbon_analyzer.py');
    }

    const filePath = doc.uri.fsPath;

    let stdout = '';
    let stderr = '';

    const proc = spawn(pythonPath, [scriptPath, '--json', filePath], {
        cwd: path.dirname(scriptPath),
    });

    proc.stdout.on('data', chunk => stdout += chunk.toString());
    proc.stderr.on('data', chunk => stderr += chunk.toString());

    proc.on('close', code => {
        if (stderr) {
            // Only log actual errors, not codecarbon INFO noise
            const realErrors = stderr.split('\n')
                .filter(l => !l.includes('[codecarbon') && l.trim())
                .join('\n');
            if (realErrors) console.error('Analyzer stderr:', realErrors);
        }

        let findings = [];
        try {
            const parsed = JSON.parse(stdout);
            findings = parsed.findings || [];
        } catch (e) {
            console.error('Carbon Analyzer: could not parse JSON output:', stdout.slice(0, 200));
            return;
        }

        // Cache and apply
        findingsCache.set(doc.uri.toString(), findings);

        const editor = vscode.window.visibleTextEditors
            .find(e => e.document.uri.toString() === doc.uri.toString());
        if (editor) applyDecorations(editor, findings);

        applyDiagnostics(doc, findings);
    });
}


// ─────────────────────────────────────────────────────────────────────────
// APPLY DECORATIONS  ("⚡ green fix" label at end of first line of block)
// ─────────────────────────────────────────────────────────────────────────
function applyDecorations(editor, findings) {
    const ranges = findings.map(f => {
        // line is 1-based in our JSON; VS Code ranges are 0-based
        const lineIndex = Math.max(0, f.line - 1);
        const lineText  = editor.document.lineAt(lineIndex).text;
        const endChar   = lineText.length;
        return new vscode.Range(lineIndex, endChar, lineIndex, endChar);
    });

    editor.setDecorations(GREEN_FIX_DECO, ranges);
}


// ─────────────────────────────────────────────────────────────────────────
// APPLY DIAGNOSTICS  (info squiggle under first line, shows in Problems)
// ─────────────────────────────────────────────────────────────────────────
function applyDiagnostics(doc, findings) {
    const diagnostics = findings.map(f => {
        const lineIndex = Math.max(0, f.line - 1);
        const lineText  = doc.lineAt(lineIndex).text;
        const range     = new vscode.Range(
            lineIndex, 0,
            lineIndex, lineText.trimEnd().length
        );

        const msg  = `[Carbon] ${f.group} — ${f.reduction_pct ? f.reduction_pct + '% lower CO2 possible' : 'optimization available'}`;
        const diag = new vscode.Diagnostic(range, msg, vscode.DiagnosticSeverity.Information);
        diag.source = 'carbon-analyzer';
        diag.code   = f.dirty_op;
        return diag;
    });

    DIAG_COLLECTION.set(doc.uri, diagnostics);
}


// ─────────────────────────────────────────────────────────────────────────
// HOVER PROVIDER  (rich tooltip when hovering over a flagged line)
// ─────────────────────────────────────────────────────────────────────────
function provideHover(document, position) {
    const findings = findingsCache.get(document.uri.toString());
    if (!findings || findings.length === 0) return null;

    // Find a finding whose block contains the hovered line (1-based)
    const hoveredLine = position.line + 1;   // convert back to 1-based
    const f = findings.find(f => hoveredLine >= f.line && hoveredLine <= f.end_line);
    if (!f) return null;

    const confIcon = f.confidence === 'CONFIRMED' ? '🟢' :
                     f.confidence === 'LIKELY'    ? '🟡' : '🟡';

    const reductionLine = f.reduction_pct
        ? `**CO2 reduction: ${f.reduction_pct}%**  ${progressBar(f.reduction_pct)}`
        : '';

    const co2Line = (f.dirty_co2_fmt && f.clean_co2_fmt)
        ? `| | CO2 per call |\n|---|---|\n| Current \`(${f.dirty_op})\` | \`${f.dirty_co2_fmt}\` |\n| Optimized \`(${f.clean_op})\` | \`${f.clean_co2_fmt}\` |`
        : '';

    const suggestionCode = f.suggestion
        .split('\n')
        .map(l => l.startsWith('    ') ? l.slice(4) : l)
        .join('\n');

    const markdown = new vscode.MarkdownString(
`### ⚡ Green Fix Available
${confIcon} **${f.group}** &nbsp;·&nbsp; ${f.confidence} &nbsp;·&nbsp; est. N = ${f.estimated_n.toLocaleString()}

---
**Issue:** ${f.description}

${co2Line}

${reductionLine}

---
**Quick Fix:**
\`\`\`python
${suggestionCode}
\`\`\`

---
*Carbon-Aware Code Analyzer · India Grid (IND) · ~708 gCO2eq/kWh*`
    );

    markdown.isTrusted = true;
    markdown.supportHtml = false;

    // Hover range covers the entire flagged block
    const startLine = Math.max(0, f.line - 1);
    const endLine   = Math.max(0, f.end_line - 1);
    const hoverRange = new vscode.Range(
        startLine, 0,
        endLine,   document.lineAt(endLine).text.length
    );

    return new vscode.Hover(markdown, hoverRange);
}


// ─────────────────────────────────────────────────────────────────────────
// HELPERS
// ─────────────────────────────────────────────────────────────────────────
function progressBar(pct) {
    const filled = Math.round(pct / 10);
    return '█'.repeat(filled) + '░'.repeat(10 - filled);
}


function deactivate() {
    DIAG_COLLECTION.clear();
    DIAG_COLLECTION.dispose();
}


module.exports = { activate, deactivate };
