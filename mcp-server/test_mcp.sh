#!/bin/bash
# Test bountyapi-mcp server end-to-end via MCP JSON-RPC protocol
# Sends: initialize -> tools/list -> tools/call (stamp duty)
# The server talks to the LIVE bountyapi.com API

cd /c/Users/vncen/saas/asia-data-api/mcp-server

echo "=== 1. Initialize handshake ==="
echo '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"test-client","version":"1.0.0"}}}' | node dist/index.js 2>/dev/null | head -1 | python -m json.tool 2>/dev/null || echo "(raw output shown)"

echo ""
echo "=== 2. List available tools ==="
printf '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"test","version":"1.0.0"}}}\n{"jsonrpc":"2.0","method":"notifications/initialized"}\n{"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}}\n' | node dist/index.js 2>/dev/null | tail -1 | python -c "import sys,json; data=json.load(sys.stdin); [print(f'  - {t[\"name\"]}: {t[\"description\"][:80]}') for t in data.get('result',{}).get('tools',[])]" 2>/dev/null || echo "(checking raw)"

echo ""
echo "=== 3. Call sg_stamp_duty (live API call) ==="
printf '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"test","version":"1.0.0"}}}\n{"jsonrpc":"2.0","method":"notifications/initialized"}\n{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"sg_stamp_duty","arguments":{"price":1500000,"buyer_profile":"SC","property_count":1}}}\n' | timeout 15 node dist/index.js 2>/dev/null | tail -1 | python -m json.tool 2>/dev/null || echo "(raw shown above)"

echo ""
echo "=== 4. Call sg_rental_yield (live API call) ==="
printf '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"test","version":"1.0.0"}}}\n{"jsonrpc":"2.0","method":"notifications/initialized"}\n{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"sg_rental_yield","arguments":{"property_price":1200000,"monthly_rent":4200}}}\n' | timeout 15 node dist/index.js 2>/dev/null | tail -1 | python -m json.tool 2>/dev/null || echo "(raw shown above)"

echo ""
echo "=== Done ==="
