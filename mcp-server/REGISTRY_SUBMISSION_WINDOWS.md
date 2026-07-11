# Official MCP Registry Submission — Windows PowerShell

Use this from PowerShell. Important: PowerShell aliases `curl` to `Invoke-WebRequest`, so `curl -L` fails. Use `curl.exe` explicitly.

## 1) Go to the D-drive repo

```powershell
cd D:\vncen\saas\bounty-api-fresh\mcp-server
```

## 2) Download the publisher

```powershell
curl.exe -L "https://github.com/modelcontextprotocol/registry/releases/latest/download/mcp-publisher_windows_amd64.tar.gz" -o mcp-publisher.tar.gz
```

## 3) Extract it

```powershell
tar -xzf mcp-publisher.tar.gz mcp-publisher.exe
```

## 4) Validate server.json

```powershell
.\mcp-publisher.exe validate server.json
```

Expected result:

```text
Validating against https://registry.modelcontextprotocol.io...
✅ server.json is valid
```

## 5) Login with GitHub

```powershell
.\mcp-publisher.exe login github
```

Follow the browser/device-code flow.

## 6) Publish

```powershell
.\mcp-publisher.exe publish server.json
```

## Notes

- Do not run this from `C:\Users\vncen\saas\asia-data-api`; the working repo is now on D drive.
- Current validated metadata: `io.github.vncent786/bounty-api`, version `1.8.0`, 27 MCP tools, npm package `bountyapi-mcp@1.8.0`.
- If you see `A parameter cannot be found that matches parameter name 'L'`, you used PowerShell's `curl` alias. Use `curl.exe`.
