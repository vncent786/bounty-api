#!/usr/bin/env node

/**
 * BountyAPI MCP Server v1.1.0
 *
 * Exposes specialist data APIs as MCP tools for AI agents.
 * Supports x402 micropayments — agents can automatically pay for premium endpoints.
 *
 * Usage (free tools only):
 *   npx bountyapi-mcp
 *
 * Usage (with payment support for paid endpoints):
 *   EVM_PRIVATE_KEY=0x... npx bountyapi-mcp
 *   MAX_SPEND_USD=5.00 EVM_PRIVATE_KEY=0x... npx bountyapi-mcp
 *
 * Claude Desktop config:
 *   {
 *     "mcpServers": {
 *       "bounty": {
 *         "command": "npx",
 *         "args": ["bountyapi-mcp"],
 *         "env": {
 *           "EVM_PRIVATE_KEY": "0x...",
 *           "MAX_SPEND_USD": "1.00"
 *         }
 *       }
 *     }
 *   }
 */

import { Server } from "@modelcontextprotocol/sdk/server/index.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import { CallToolRequestSchema, ListToolsRequestSchema } from "@modelcontextprotocol/sdk/types.js";
import { privateKeyToAccount } from "viem/accounts";
import { x402Client, x402HTTPClient } from "@x402/core/client";
import { registerExactEvmScheme } from "@x402/evm/exact/client";

// ============================================================
// Configuration
// ============================================================

const API_BASE = process.env.BOUNTY_API_URL || "https://bountyapi.com";
const rawPrivateKey = process.env.EVM_PRIVATE_KEY || "";
const MAX_SPEND_USD = parseFloat(process.env.MAX_SPEND_USD || "1.00");

// Normalize private key (ensure 0x prefix — MetaMask sometimes omits it)
const PRIVATE_KEY = rawPrivateKey && !rawPrivateKey.startsWith("0x")
  ? "0x" + rawPrivateKey
  : rawPrivateKey;

// ============================================================
// x402 Payment Client Setup
// ============================================================

let paymentClient: x402Client | null = null;
let paymentHttp: x402HTTPClient | null = null;
let walletAddress: string | null = null;

if (PRIVATE_KEY) {
  try {
    const account = privateKeyToAccount(PRIVATE_KEY as `0x${string}`);
    walletAddress = account.address;
    paymentClient = new x402Client();
    registerExactEvmScheme(paymentClient, { signer: account });
    paymentHttp = new x402HTTPClient(paymentClient);
    console.error(`[bountyapi-mcp] Wallet connected: ${walletAddress}`);
    console.error(`[bountyapi-mcp] Max spend per request: $${MAX_SPEND_USD}`);
  } catch (err) {
    console.error(`[bountyapi-mcp] WARNING: Failed to initialize wallet: ${err instanceof Error ? err.message : err}`);
    console.error(`[bountyapi-mcp] Running in free-only mode.`);
  }
} else {
  console.error(`[bountyapi-mcp] No EVM_PRIVATE_KEY set. Free endpoints only.`);
  console.error(`[bountyapi-mcp] To enable paid endpoints: EVM_PRIVATE_KEY=0x... npx bountyapi-mcp`);
}

// ============================================================
// Tool Definitions
// ============================================================

const FREE_BADGE = "(FREE)";
const PAID_BADGE = "(PAID — auto-pay if wallet configured)";

const TOOLS = [
  {
    name: "sg_stamp_duty",
    description: `Calculate Singapore property stamp duty (BSD + ABSD). Returns total stamp duty, effective rate, and breakdown by tier. Buyer profiles: SC (Singapore Citizen), SPR (Permanent Resident), FR (Foreigner), entity, developer, trustee. Rates verified against IRAS (iras.gov.sg). ${FREE_BADGE}`,
    inputSchema: {
      type: "object",
      properties: {
        price: { type: "number", description: "Property purchase price in SGD, e.g. 1500000" },
        property_type: { type: "string", enum: ["residential", "non-residential"], default: "residential" },
        buyer_profile: { type: "string", enum: ["SC", "SPR", "FR", "entity", "developer", "trustee"], default: "SC" },
        property_count: { type: "integer", description: "Number of residential properties owned including this one", default: 1, minimum: 1 }
      },
      required: ["price"]
    }
  },
  {
    name: "sg_postal_lookup",
    description: `Look up Singapore postal code to find the district number, district name, and area names. Covers all 28 postal districts. ${FREE_BADGE}`,
    inputSchema: {
      type: "object",
      properties: {
        postal_code: { type: "string", description: "Singapore postal code (6 digits), e.g. 238801" }
      },
      required: ["postal_code"]
    }
  },
  {
    name: "sg_rental_yield",
    description: `Calculate rental investment metrics for a Singapore property. Returns gross yield, net yield, cap rate, price-to-rent ratio, monthly cashflow, and years to break even. ${PAID_BADGE}`,
    inputSchema: {
      type: "object",
      properties: {
        property_price: { type: "number", description: "Property purchase price in SGD" },
        monthly_rent: { type: "number", description: "Expected monthly rental income in SGD" },
        management_fee_monthly: { type: "number", description: "Monthly property management fee (optional)", default: 0 },
        maintenance_monthly: { type: "number", description: "Monthly maintenance cost (optional)", default: 0 },
        annual_expenses: { type: "number", description: "Other annual expenses (optional)", default: 0 }
      },
      required: ["property_price", "monthly_rent"]
    }
  },
  {
    name: "hdb_resale_median",
    description: `Get HDB resale price data for a Singapore town. Returns median prices by flat type (2 ROOM through EXECUTIVE) with transaction counts. Data sourced from data.gov.sg. ${PAID_BADGE}`,
    inputSchema: {
      type: "object",
      properties: {
        town: { type: "string", description: "Town name (e.g. 'ANG MO KIO', 'BISHAN', 'TAMPINES'). Use uppercase." }
      },
      required: ["town"]
    }
  },
  {
    name: "hdb_resale_search",
    description: `Search HDB resale transactions with filters. Returns individual transaction records with flat type, storey, area, and price. ${PAID_BADGE}`,
    inputSchema: {
      type: "object",
      properties: {
        town: { type: "string", description: "Filter by town (optional)" },
        flat_type: { type: "string", description: "Filter by flat type, e.g. '4 ROOM' (optional)" },
        min_price: { type: "number", description: "Minimum price in SGD (optional)" },
        max_price: { type: "number", description: "Maximum price in SGD (optional)" },
        limit: { type: "integer", description: "Max results (default 20, max 100)", default: 20 }
      }
    }
  }
];

// ============================================================
// Payment-Aware API Call
// ============================================================

/**
 * Parse a human-readable price from the 402 response for error messages.
 * Returns something like "$0.01" or falls back to the raw amount.
 */
function extractPriceLabel(paymentRequired: any): string {
  try {
    const req = paymentRequired.accepts?.[0];
    if (!req) return "unknown";
    // amount is in atomic units; USDC has 6 decimals on Base
    const amountRaw = parseFloat(req.amount);
    if (!isNaN(amountRaw)) {
      const usd = amountRaw / 1_000_000;
      return `$${usd.toFixed(4)}`;
    }
    return req.price || "unknown";
  } catch {
    return "unknown";
  }
}

/**
 * Makes an API call. If the endpoint returns 402 Payment Required:
 * - If no wallet is configured → clear error explaining how to set up
 * - If wallet is configured → automatically pay and retry
 * - If price exceeds MAX_SPEND_USD → abort with clear message
 */
async function callAPI(path: string, method = "GET", body: unknown = null): Promise<unknown> {
  const url = `${API_BASE}${path}`;
  const options: RequestInit = {
    method,
    headers: { "Content-Type": "application/json" },
  };
  if (body) {
    options.body = JSON.stringify(body);
  }

  // First attempt
  const response = await fetch(url, options);

  // Not a payment challenge — return normally
  if (response.status !== 402) {
    if (!response.ok) {
      const errorText = await response.text();
      throw new Error(`API error ${response.status}: ${errorText.substring(0, 300)}`);
    }
    return response.json();
  }

  // ============================================================
  // 402 Payment Required — handle the challenge
  // ============================================================

  // Parse payment requirements from the response
  let paymentRequired: any;
  try {
    const getHeader = (name: string) => response.headers.get(name);
    let bodyJson: unknown;
    try {
      const text = await response.clone().text();
      if (text) bodyJson = JSON.parse(text);
    } catch { /* no body or not JSON */ }

    if (!paymentHttp) throw new Error("no wallet");
    paymentRequired = paymentHttp.getPaymentRequiredResponse(getHeader, bodyJson);
  } catch {
    // Can't parse the payment challenge at all
    throw new Error(
      "This endpoint requires payment but the payment challenge could not be parsed. " +
      "The server may be misconfigured."
    );
  }

  const priceLabel = extractPriceLabel(paymentRequired);

  // Check 1: Is a wallet configured?
  if (!paymentClient || !paymentHttp || !walletAddress) {
    throw new Error(
      `PAYMENT REQUIRED: This endpoint costs ${priceLabel} USDC on Base. ` +
      `No wallet is configured. To enable automatic payment:\n\n` +
      `1. Create a burner wallet on Base with a few dollars of USDC\n` +
      `2. Set the EVM_PRIVATE_KEY environment variable:\n` +
      `   EVM_PRIVATE_KEY=0xyour_private_key npx bountyapi-mcp\n\n` +
      `Or in Claude Desktop config:\n` +
      `   "env": { "EVM_PRIVATE_KEY": "0x..." }`
    );
  }

  // Check 2: Does the price exceed the spend limit?
  const acceptedReq = paymentRequired.accepts?.[0];
  if (acceptedReq?.amount) {
    const amountRaw = parseFloat(acceptedReq.amount);
    if (!isNaN(amountRaw)) {
      const priceUsd = amountRaw / 1_000_000; // USDC 6 decimals
      if (priceUsd > MAX_SPEND_USD) {
        throw new Error(
          `PAYMENT BLOCKED: Endpoint costs $${priceUsd.toFixed(4)} ` +
          `which exceeds your MAX_SPEND_USD limit of $${MAX_SPEND_USD.toFixed(2)}. ` +
          `To allow this payment, increase the limit:\n` +
          `   MAX_SPEND_USD=${(priceUsd * 2).toFixed(2)} npx bountyapi-mcp`
        );
      }
    }
  }

  // Create payment payload and retry
  console.error(`[bountyapi-mcp] Paying ${priceLabel} for ${path}...`);

  const paymentPayload = await paymentClient.createPaymentPayload(paymentRequired);
  const paymentHeaders = paymentHttp.encodePaymentSignatureHeader(paymentPayload);

  const retryRequest = new Request(url, options);
  for (const [key, value] of Object.entries(paymentHeaders)) {
    retryRequest.headers.set(key, value as string);
  }
  retryRequest.headers.set("Access-Control-Expose-Headers", "PAYMENT-RESPONSE,X-PAYMENT-RESPONSE");

  const paidResponse = await fetch(retryRequest);

  if (!paidResponse.ok) {
    const errorText = await paidResponse.text();
    throw new Error(
      `Payment failed (status ${paidResponse.status}): ${errorText.substring(0, 300)}`
    );
  }

  // Log successful payment
  const settleHeader =
    paidResponse.headers.get("PAYMENT-RESPONSE") ||
    paidResponse.headers.get("X-PAYMENT-RESPONSE");

  if (settleHeader) {
    console.error(`[bountyapi-mcp] ✓ Payment settled: ${priceLabel} USDC on Base`);
    try {
      const settle = JSON.parse(Buffer.from(settleHeader, "base64").toString());
      if (settle.transaction) {
        console.error(`[bountyapi-mcp] TX: https://basescan.org/tx/${settle.transaction}`);
      }
    } catch { /* non-critical */ }
  }

  return paidResponse.json();
}

// ============================================================
// MCP Server
// ============================================================

const server = new Server(
  { name: "bountyapi-mcp", version: "1.1.0" },
  { capabilities: { tools: {} } }
);

// List tools
server.setRequestHandler(ListToolsRequestSchema, async () => ({
  tools: TOOLS
}));

// Handle tool calls
server.setRequestHandler(CallToolRequestSchema, async (request) => {
  const { name, arguments: args } = request.params;
  const toolArgs = (args ?? {}) as Record<string, unknown>;

  try {
    let result;

    switch (name) {
      case "sg_stamp_duty":
        result = await callAPI("/stamp-duty", "POST", {
          price: toolArgs.price,
          property_type: toolArgs.property_type || "residential",
          buyer_profile: toolArgs.buyer_profile || "SC",
          property_count: toolArgs.property_count || 1
        });
        break;

      case "sg_postal_lookup":
        result = await callAPI(`/postal/${toolArgs.postal_code}`);
        break;

      case "sg_rental_yield":
        result = await callAPI("/rental-yield/calculate", "POST", {
          property_price: toolArgs.property_price,
          monthly_rent: toolArgs.monthly_rent,
          management_fee_monthly: toolArgs.management_fee_monthly || 0,
          maintenance_monthly: toolArgs.maintenance_monthly || 0,
          annual_expenses: toolArgs.annual_expenses || 0
        });
        break;

      case "hdb_resale_median":
        result = await callAPI(`/hdb/median/${encodeURIComponent(toolArgs.town as string)}`);
        break;

      case "hdb_resale_search": {
        const params = new URLSearchParams();
        if (toolArgs.town) params.set("town", toolArgs.town as string);
        if (toolArgs.flat_type) params.set("flat_type", toolArgs.flat_type as string);
        if (toolArgs.min_price) params.set("min_price", String(toolArgs.min_price));
        if (toolArgs.max_price) params.set("max_price", String(toolArgs.max_price));
        params.set("limit", String(toolArgs.limit || 20));
        result = await callAPI(`/hdb/search?${params}`);
        break;
      }

      default:
        throw new Error(`Unknown tool: ${name}`);
    }

    return {
      content: [{ type: "text", text: JSON.stringify(result, null, 2) }]
    };
  } catch (err) {
    const message = err instanceof Error ? err.message : String(err);
    return {
      content: [{ type: "text", text: `Error: ${message}` }],
      isError: true
    };
  }
});

// Start server
const transport = new StdioServerTransport();
await server.connect(transport);
console.error(`bountyapi-mcp v1.1.0 running on stdio`);
console.error(`API base: ${API_BASE}`);
console.error(`Payment: ${walletAddress ? "enabled" : "disabled (free endpoints only)"}`);
