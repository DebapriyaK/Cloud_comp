# Carbon-Aware Analyzer VS Code Demo

This extension uses Docker and DynamoDB by default. The user does not need the
analyzer Python files locally.

## One-time setup

1. Install and start Docker Desktop.

2. Make sure the analyzer image exists:

   ```powershell
   docker build -t carbon-aware-analyzer:latest ..
   ```

   Run that command from inside the `vscode-extension` folder, or build from
   the repository root with:

   ```powershell
   docker build -t carbon-aware-analyzer:latest .
   ```

3. Install the packaged extension:

   ```powershell
   code --install-extension .\carbon-aware-analyzer-0.1.0.vsix --force
   ```

## Use

Configure DynamoDB access in VS Code settings:

```json
{
  "carbonAnalyzer.executionMode": "docker",
  "carbonAnalyzer.dockerImage": "carbon-aware-analyzer:latest",
  "carbonAnalyzer.dynamoDbTable": "carbon_emissions",
  "carbonAnalyzer.awsRegion": "ap-south-1",
  "carbonAnalyzer.awsAccessKeyId": "YOUR_AWS_ACCESS_KEY_ID",
  "carbonAnalyzer.awsSecretAccessKey": "YOUR_AWS_SECRET_ACCESS_KEY"
}
```

If you use temporary AWS credentials, also set:

```json
{
  "carbonAnalyzer.awsSessionToken": "YOUR_AWS_SESSION_TOKEN"
}
```

Open any Python file in a normal VS Code window and save it.

The extension runs:

```text
docker run --rm -v <user-file-folder>:/workspace:ro -e DYNAMODB_TABLE=... -e AWS_REGION=... carbon-aware-analyzer:latest python /app/carbon_analyzer.py --json /workspace/<file>.py
```

The JSON result is shown as green line hints, hover text, and Problems panel
diagnostics.

Amber scheduling advice also needs an ElectricityMaps API key in VS Code
settings:

```json
{
  "carbonAnalyzer.electricityMapsApiKey": "YOUR_KEY",
  "carbonAnalyzer.gridZone": "IN-SO"
}
```
