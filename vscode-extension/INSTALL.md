# How to load the extension into VS Code

## Option A — Quickest (copy to extensions folder)

1. Open a terminal and run:

   ```
   xcopy /E /I "d:\College Stuff\SEM6\cc\final\vscode-extension" "%USERPROFILE%\.vscode\extensions\carbon-aware-analyzer"
   ```

2. Restart VS Code completely (close and reopen).

3. Open any Python file, write some code, and hit **Ctrl+S**.

---

## Option B — Developer mode (no copy needed, easier to update)

1. Open the `vscode-extension` folder in VS Code:
   ```
   code "d:\College Stuff\SEM6\cc\final\vscode-extension"
   ```

2. Press **F5** — this opens a new VS Code window (Extension Development Host)
   with the extension already loaded.

3. In that new window, open any Python file and save it.

---

## Verify it works

Open any Python file with inefficient code and save.  
You should see **⚡ green fix** in green italic at the end of flagged lines.  
Hover over the line to see the full carbon comparison and quick fix.
