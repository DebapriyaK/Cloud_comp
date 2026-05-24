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
 *   3. For moderate/heavy workloads (ML, data-processing code) it also:
 *        • Calls grid_advisor.py to fetch ElectricityMaps forecast.
 *        • Adds an amber "🌱 run greener" decoration on line 1.
 *        • Shows a scheduling hover tooltip on line 1 with: best run time
 *          (in IST), current vs best intensity, % reduction.
 *        • If deploy signals detected (boto3, Dockerfile, etc.): shows
 *          greenest cloud region recommendations.
 *   4. Everything clears and re-runs on the next save.
 */

const vscode  = require('vscode');
const { spawn } = require('child_process');
const path    = require('path');
const fs      = require('fs');
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
    backgroundColor:  'rgba(78, 201, 78, 0.07)',
    isWholeLine:       false,
});

// ── Decoration type for the amber "🌱 run greener" label on line 1 ────────
const GREEN_SCHEDULE_DECO = vscode.window.createTextEditorDecorationType({
    after: {
        contentText:     '  🌱 run greener',
        color:           '#E6B84A',   // amber/golden
        fontStyle:       'italic',
        fontWeight:      'normal',
        margin:          '0 0 0 12px',
    },
    backgroundColor:  'rgba(230, 184, 74, 0.07)',
    isWholeLine:       false,
});

// ── Diagnostic collection (Problems panel) ───────────────────────────────
const DIAG_COLLECTION = vscode.languages.createDiagnosticCollection('carbon-analyzer');

// ── In-memory caches ─────────────────────────────────────────────────────
const findingsCache = new Map();   // uri -> findings[]
const gridCache     = new Map();   // uri -> grid advice object

// ── Debounce timer ────────────────────────────────────────────────────────
let debounceTimer = null;

const CONTAINER_APP_DIR = '/app';
const CONTAINER_WORKSPACE = '/workspace';

const ENV_PASSTHROUGH = [
    'ELECTRICITYMAPS_LAT',
    'ELECTRICITYMAPS_LON',
    'CARBON_ANALYZER_LAT',
    'CARBON_ANALYZER_LON',
];

const CONFIG_ENV = [
    ['DYNAMODB_TABLE', 'dynamoDbTable'],
    ['AWS_REGION', 'awsRegion'],
    ['AWS_ACCESS_KEY_ID', 'awsAccessKeyId'],
    ['AWS_SECRET_ACCESS_KEY', 'awsSecretAccessKey'],
    ['AWS_SESSION_TOKEN', 'awsSessionToken'],
];

function resolveAnalyzerScript(config) {
    const scriptPath = config.get('analyzerScript') || '';
    return scriptPath || path.join(__dirname, '..', 'carbon_analyzer.py');
}

function getExecutionMode(config, scriptPath) {
    const configured = config.get('executionMode') || 'auto';
    if (configured === 'local' || configured === 'docker') return configured;
    return fs.existsSync(scriptPath) ? 'local' : 'docker';
}

function getDockerExecutable(config) {
    return config.get('dockerExecutable') || 'docker';
}

function getDockerImage(config) {
    return config.get('dockerImage') || 'carbon-aware-analyzer:latest';
}

function addEnvArg(args, name, value) {
    if (value) args.push('-e', `${name}=${value}`);
}

function getDockerEnvArgs(config) {
    const args = [];
    for (const [envName, settingName] of CONFIG_ENV) {
        addEnvArg(args, envName, config.get(settingName) || process.env[envName]);
    }
    for (const name of ENV_PASSTHROUGH) {
        addEnvArg(args, name, process.env[name]);
    }
    return args;
}

function spawnAnalyzerProcess(config, filePath) {
    const scriptPath = resolveAnalyzerScript(config);
    const mode = getExecutionMode(config, scriptPath);

    if (mode === 'docker') {
        const fileDir = path.dirname(filePath);
        const containerPath = path.posix.join(CONTAINER_WORKSPACE, path.basename(filePath));
        const args = [
            'run', '--rm',
            '-v', `${fileDir}:${CONTAINER_WORKSPACE}:ro`,
            ...getDockerEnvArgs(config),
            getDockerImage(config),
            'python', path.posix.join(CONTAINER_APP_DIR, 'carbon_analyzer.py'),
            '--json', containerPath,
        ];

        return {
            proc: spawn(getDockerExecutable(config), args, { cwd: fileDir }),
            label: 'Docker analyzer',
        };
    }

    const pythonPath = config.get('pythonPath') || 'python';
    return {
        proc: spawn(pythonPath, [scriptPath, '--json', filePath], {
            cwd: path.dirname(scriptPath),
        }),
        label: 'local analyzer',
    };
}

function spawnGridAdvisorProcess(config, advisorArgs) {
    const scriptPath = resolveAnalyzerScript(config);
    const mode = getExecutionMode(config, scriptPath);

    if (mode === 'docker') {
        const args = [
            'run', '--rm',
            ...getDockerEnvArgs(config),
            getDockerImage(config),
            'python', path.posix.join(CONTAINER_APP_DIR, 'grid_advisor.py'),
            ...advisorArgs,
        ];

        return {
            proc: spawn(getDockerExecutable(config), args),
            label: 'Docker grid advisor',
        };
    }

    const pythonPath = config.get('pythonPath') || 'python';
    const baseDir = path.dirname(scriptPath);
    const advisorPath = path.join(baseDir, 'grid_advisor.py');
    return {
        proc: spawn(pythonPath, [advisorPath, ...advisorArgs], { cwd: baseDir }),
        label: 'local grid advisor',
    };
}


// ─────────────────────────────────────────────────────────────────────────
// ACTIVATE
// ─────────────────────────────────────────────────────────────────────────
function activate(context) {
    console.log('Carbon-Aware Analyzer: activated');

    context.subscriptions.push(
        vscode.workspace.onDidSaveTextDocument(doc => {
            if (doc.languageId === 'python') runAnalyzer(doc);
        })
    );

    context.subscriptions.push(
        vscode.workspace.onDidOpenTextDocument(doc => {
            if (doc.languageId === 'python') runAnalyzer(doc);
        })
    );

    context.subscriptions.push(
        vscode.window.onDidChangeActiveTextEditor(editor => {
            if (editor && editor.document.languageId === 'python') {
                const uri = editor.document.uri.toString();
                const cachedFindings = findingsCache.get(uri);
                const cachedGrid     = gridCache.get(uri);
                if (cachedFindings) applyDecorations(editor, cachedFindings, cachedGrid);
            }
        })
    );

    vscode.workspace.textDocuments.forEach(doc => {
        if (doc.languageId === 'python') runAnalyzer(doc);
    });

    context.subscriptions.push(
        vscode.languages.registerHoverProvider(
            { language: 'python' },
            { provideHover }
        )
    );

    context.subscriptions.push(DIAG_COLLECTION);
    context.subscriptions.push(GREEN_FIX_DECO);
    context.subscriptions.push(GREEN_SCHEDULE_DECO);
}


// ─────────────────────────────────────────────────────────────────────────
// RUN THE PYTHON ANALYZER
// ─────────────────────────────────────────────────────────────────────────
function runAnalyzer(doc) {
    if (doc.uri.scheme !== 'file') return;

    const config = vscode.workspace.getConfiguration('carbonAnalyzer');
    const filePath = doc.uri.fsPath;
    let stdout = '';
    let stderr = '';

    const { proc, label } = spawnAnalyzerProcess(config, filePath);

    proc.stdout.on('data', chunk => stdout += chunk.toString());
    proc.stderr.on('data', chunk => stderr += chunk.toString());

    proc.on('error', err => {
        console.error(`Carbon Analyzer: failed to start ${label}:`, err.message);
    });

    proc.on('close', code => {
        if (stderr) {
            const realErrors = stderr.split('\n')
                .filter(l => !l.includes('[codecarbon') && l.trim())
                .join('\n');
            if (realErrors) console.error('Analyzer stderr:', realErrors);
        }
        if (code !== 0 && !stdout.trim()) {
            console.error(`Carbon Analyzer: ${label} exited with code ${code}`);
            return;
        }

        let findings = [];
        let workload = null;
        try {
            const parsed = JSON.parse(stdout);
            findings = parsed.findings || [];
            workload = parsed.workload || null;
        } catch (e) {
            console.error('Carbon Analyzer: could not parse JSON output:', stdout.slice(0, 200));
            return;
        }

        findingsCache.set(doc.uri.toString(), findings);

        const editor = vscode.window.visibleTextEditors
            .find(e => e.document.uri.toString() === doc.uri.toString());
        if (editor) applyDecorations(editor, findings, gridCache.get(doc.uri.toString()));

        applyDiagnostics(doc, findings);

        // Kick off grid advisor for moderate/heavy workloads
        if (workload && (workload.tier === 'heavy' || workload.tier === 'moderate')) {
            runGridAdvisor(doc, workload);
        } else {
            // Clear any stale grid decoration
            gridCache.delete(doc.uri.toString());
            if (editor) applyDecorations(editor, findings, null);
        }
    });
}


// ─────────────────────────────────────────────────────────────────────────
// RUN GRID ADVISOR
// ─────────────────────────────────────────────────────────────────────────
function runGridAdvisor(doc, workload) {
    const config     = vscode.workspace.getConfiguration('carbonAnalyzer');
    const apiKey     = config.get('electricityMapsApiKey') || '';
    const zone       = config.get('gridZone') || 'IN-SO';

    if (!apiKey) return;   // silently skip if not configured

    const args = ['--zone', zone, '--key', apiKey];
    // Always include live cloud-region recommendations for non-light workloads.
    // (Previously gated on deploy-signal detection only.)
    if (workload && (workload.tier === 'heavy' || workload.tier === 'moderate')) {
        args.push('--deploy');
    }

    let stdout = '';
    let stderr = '';

    const { proc, label } = spawnGridAdvisorProcess(config, args);

    proc.stdout.on('data', chunk => stdout += chunk.toString());
    proc.stderr.on('data', chunk => stderr += chunk.toString());

    proc.on('error', err => {
        console.error(`Grid Advisor: failed to start ${label}:`, err.message);
    });

    proc.on('close', code => {
        if (stderr && stderr.trim()) {
            console.error('Grid Advisor stderr:', stderr.trim());
        }
        if (code !== 0 && !stdout.trim()) {
            console.error(`Grid Advisor: ${label} exited with code ${code}`);
            return;
        }

        let advice = null;
        try {
            advice = JSON.parse(stdout);
        } catch (e) {
            console.error('Grid Advisor: could not parse JSON:', stdout.slice(0, 200));
            return;
        }

        if (advice && !advice.error) {
            gridCache.set(doc.uri.toString(), advice);

            const editor = vscode.window.visibleTextEditors
                .find(e => e.document.uri.toString() === doc.uri.toString());
            const findings = findingsCache.get(doc.uri.toString()) || [];
            if (editor) applyDecorations(editor, findings, advice);
        }
    });
}


// ─────────────────────────────────────────────────────────────────────────
// APPLY DECORATIONS
// ─────────────────────────────────────────────────────────────────────────
function applyDecorations(editor, findings, gridAdvice) {
    // Green fix decorations on each flagged line
    const fixRanges = findings.map(f => {
        const lineIndex = Math.max(0, f.line - 1);
        const lineText  = editor.document.lineAt(lineIndex).text;
        const endChar   = lineText.length;
        return new vscode.Range(lineIndex, endChar, lineIndex, endChar);
    });
    editor.setDecorations(GREEN_FIX_DECO, fixRanges);

    // Schedule decoration on line 0 (first line of file) when grid advice available
    if (gridAdvice && gridAdvice.best_time && gridAdvice.best_time.time_ist) {
        const firstLine = editor.document.lineAt(0).text;
        const endChar   = firstLine.length;
        const schedRange = new vscode.Range(0, endChar, 0, endChar);
        editor.setDecorations(GREEN_SCHEDULE_DECO, [schedRange]);
    } else {
        editor.setDecorations(GREEN_SCHEDULE_DECO, []);
    }
}


// ─────────────────────────────────────────────────────────────────────────
// APPLY DIAGNOSTICS
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
// HOVER PROVIDER
// ─────────────────────────────────────────────────────────────────────────
function provideHover(document, position) {
    const uri      = document.uri.toString();
    const findings = findingsCache.get(uri);
    const advice   = gridCache.get(uri);

    // ── Grid advice hover: triggered on line 0 when advice is present ──────
    if (position.line === 0 && advice && advice.best_time && advice.best_time.time_ist) {
        return buildGridHover(document, advice);
    }

    // ── Pattern finding hover ─────────────────────────────────────────────
    if (!findings || findings.length === 0) return null;

    const hoveredLine = position.line + 1;
    const f = findings.find(f => hoveredLine >= f.line && hoveredLine <= f.end_line);
    if (!f) return null;

    return buildFindingHover(document, f);
}

function buildFindingHover(document, f) {
    const confIcon = f.confidence === 'CONFIRMED' ? '🟢' : '🟡';

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

    const startLine  = Math.max(0, f.line - 1);
    const endLine    = Math.max(0, f.end_line - 1);
    const hoverRange = new vscode.Range(
        startLine, 0,
        endLine, document.lineAt(endLine).text.length
    );

    return new vscode.Hover(markdown, hoverRange);
}

function buildGridHover(document, advice) {
    const bt = advice.best_time;
    const curr = advice.current_intensity ? `${advice.current_intensity} gCO2/kWh` : 'unknown';
    const best = bt.intensity != null ? `${bt.intensity} gCO2/kWh` : 'unknown';
    const saving = bt.reduction_pct != null ? `${bt.reduction_pct}%` : '?';

    let regionSection = '';
    if (advice.cloud_regions && advice.cloud_regions.length > 0) {
        const rows = advice.cloud_regions
            .slice(0, 4)
            .map(r => `| ${r.provider} ${r.region} | ${r.location} | ${r.intensity_gco2_kwh} gCO2/kWh | −${r.saving_pct}% |`)
            .join('\n');
        regionSection = `\n---\n**Greenest Cloud Regions for Deployment**\n\n| Region | Location | Intensity | Savings |\n|---|---|---|---|\n${rows}`;
    }

    const markdown = new vscode.MarkdownString(
`### 🌱 Run Greener — Grid Carbon Forecast

**Zone:** ${advice.zone} &nbsp;·&nbsp; **Now:** ${curr}

| Best window | Time (IST) | Intensity | CO2 saving |
|---|---|---|---|
| Next 24 h | **${bt.time_ist}** | ${best} | **−${saving}** |

> Running at **${bt.time_ist} IST** tonight uses ${saving} less carbon than running now.${regionSection}

---
*Live data via ElectricityMaps ·  Carbon-Aware Code Analyzer*`
    );

    markdown.isTrusted = true;
    markdown.supportHtml = false;

    const lineText   = document.lineAt(0).text;
    const hoverRange = new vscode.Range(0, 0, 0, lineText.length);

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
