#!/usr/bin/env node
/**
 * Test script: sends MCP JSON-RPC messages to bountyapi-mcp via stdio.
 * Tests: tool discovery, free call, and payment error handling.
 */
import { spawn } from "child_process";

const child = spawn("node", ["dist/index.js"], {
  cwd: process.cwd(),
  stdio: ["pipe", "pipe", "pipe"],
});

let buffer = "";
const responses = [];

child.stdout.on("data", (data) => {
  buffer += data.toString();
  // MCP messages are newline-delimited JSON
  const lines = buffer.split("\n");
  buffer = lines.pop() || "";
  for (const line of lines) {
    if (line.trim()) {
      try {
        responses.push(JSON.parse(line));
      } catch {}
    }
  }
});

child.stderr.on("data", (data) => {
  process.stderr.write(`[stderr] ${data}`);
});

function send(msg) {
  child.stdin.write(JSON.stringify(msg) + "\n");
}

// Wait for response
function waitForResponse(id, timeout = 10000) {
  return new Promise((resolve, reject) => {
    const start = Date.now();
    const check = setInterval(() => {
      const found = responses.find((r) => r.id === id);
      if (found) {
        clearInterval(check);
        resolve(found);
      } else if (Date.now() - start > timeout) {
        clearInterval(check);
        reject(new Error(`Timeout waiting for response id=${id}`));
      }
    }, 100);
  });
}

async function run() {
  console.log("=== Test 1: Initialize ===");
  send({
    jsonrpc: "2.0",
    id: 1,
    method: "initialize",
    params: {
      protocolVersion: "2024-11-05",
      capabilities: {},
      clientInfo: { name: "test-client", version: "1.0.0" },
    },
  });
  const init = await waitForResponse(1);
  console.log("Server info:", JSON.stringify(init.result?.serverInfo));
  console.log("Protocol:", init.result?.protocolVersion);

  console.log("\n=== Test 2: List Tools ===");
  send({ jsonrpc: "2.0", id: 2, method: "tools/list", params: {} });
  const tools = await waitForResponse(2);
  console.log(`Found ${tools.result?.tools?.length} tools:`);
  for (const tool of tools.result?.tools || []) {
    console.log(`  - ${tool.name}: ${tool.description?.substring(0, 80)}...`);
  }

  console.log("\n=== Test 3: Free Call (stamp duty) ===");
  send({
    jsonrpc: "2.0",
    id: 3,
    method: "tools/call",
    params: {
      name: "sg_stamp_duty",
      arguments: { price: 1500000, buyer_profile: "SC" },
    },
  });
  const freeResult = await waitForResponse(3);
  if (freeResult.result?.content?.[0]?.text) {
    const data = JSON.parse(freeResult.result.content[0].text);
    console.log(`BSD: $${data.bsd?.toLocaleString()}`);
    console.log(`ABSD: $${data.absd?.toLocaleString()}`);
    console.log(`Total: $${data.total_stamp_duty?.toLocaleString()}`);
  } else {
    console.log("Error:", JSON.stringify(freeResult.result));
  }

  console.log("\n=== Test 4: Paid Call WITHOUT wallet (should show clear error) ===");
  send({
    jsonrpc: "2.0",
    id: 4,
    method: "tools/call",
    params: {
      name: "hdb_resale_median",
      arguments: { town: "ANG MO KIO" },
    },
  });
  const paidError = await waitForResponse(4, 15000);
  if (paidError.result?.isError) {
    console.log("Got expected error:");
    console.log(paidError.result.content[0].text.substring(0, 200));
  } else {
    console.log("Unexpected success:", JSON.stringify(paidError.result).substring(0, 200));
  }

  console.log("\n=== All tests done ===");
  child.kill();
  process.exit(0);
}

run().catch((err) => {
  console.error("Test failed:", err);
  child.kill();
  process.exit(1);
});
