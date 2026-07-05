#!/usr/bin/env node

/**
 * Asia Data MCP Server
 * 
 * Exposes Singapore property and financial data APIs as MCP tools.
 * AI agents (Claude Desktop, Cursor, etc.) can discover and call these tools.
 * 
 * Usage:
 *   npx asia-data-mcp
 *   
 * Or add to Claude Desktop config:
 *   {
 *     "mcpServers": {
 *       "asia-data": {
 *         "command": "npx",
 *         "args": ["asia-data-mcp"]
 *       }
 *     }
 *   }
 */

import { Server } from "@modelcontextprotocol/sdk/server/index.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import { CallToolRequestSchema, ListToolsRequestSchema } from "@modelcontextprotocol/sdk/types.js";

// API base URL — change this when deployed
const API_BASE = process.env.ASIA_DATA_API_URL || "http://localhost:8000";

// ============================================================
// Tool definitions
// ============================================================

const TOOLS = [
  {
    name: "sg_stamp_duty",
    description: "Calculate Singapore property stamp duty (BSD + ABSD). Returns total stamp duty, effective rate, and breakdown by tier. Buyer profiles: SC (Singapore Citizen), SPR (Permanent Resident), FR (Foreigner), entity, developer, trustee. Rates verified against IRAS (iras.gov.sg).",
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
    description: "Look up Singapore postal code to find the district number, district name, and area names. Covers all 28 postal districts.",
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
    description: "Calculate rental investment metrics for a Singapore property. Returns gross yield, net yield, cap rate, price-to-rent ratio, monthly cashflow, and years to break even.",
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
    description: "Get HDB resale price data for a Singapore town. Returns median prices by flat type (2 ROOM through EXECUTIVE) with transaction counts. Data sourced from data.gov.sg.",
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
    description: "Search HDB resale transactions with filters. Returns individual transaction records.",
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
// API call helper
// ============================================================

async function callAPI(path, method = "GET", body = null) {
  const options = {
    method,
    headers: { "Content-Type": "application/json" },
  };
  if (body) {
    options.body = JSON.stringify(body);
  }
  
  const url = `${API_BASE}${path}`;
  const response = await fetch(url, options);
  
  if (!response.ok) {
    const errorText = await response.text();
    throw new Error(`API error ${response.status}: ${errorText.substring(0, 200)}`);
  }
  
  return response.json();
}

// ============================================================
// MCP Server setup
// ============================================================

const server = new Server(
  { name: "asia-data-mcp", version: "1.0.0" },
  { capabilities: { tools: {} } }
);

// List tools
server.setRequestHandler(ListToolsRequestSchema, async () => ({
  tools: TOOLS
}));

// Handle tool calls
server.setRequestHandler(CallToolRequestSchema, async (request) => {
  const { name, arguments: args } = request.params;

  try {
    let result;

    switch (name) {
      case "sg_stamp_duty":
        result = await callAPI("/stamp-duty", "POST", {
          price: args.price,
          property_type: args.property_type || "residential",
          buyer_profile: args.buyer_profile || "SC",
          property_count: args.property_count || 1
        });
        break;

      case "sg_postal_lookup":
        result = await callAPI(`/postal/${args.postal_code}`);
        break;

      case "sg_rental_yield":
        result = await callAPI("/rental-yield/calculate", "POST", {
          property_price: args.property_price,
          monthly_rent: args.monthly_rent,
          management_fee_monthly: args.management_fee_monthly || 0,
          maintenance_monthly: args.maintenance_monthly || 0,
          annual_expenses: args.annual_expenses || 0
        });
        break;

      case "hdb_resale_median":
        result = await callAPI(`/hdb/median/${encodeURIComponent(args.town)}`);
        break;

      case "hdb_resale_search":
        const params = new URLSearchParams();
        if (args.town) params.set("town", args.town);
        if (args.flat_type) params.set("flat_type", args.flat_type);
        if (args.min_price) params.set("min_price", args.min_price);
        if (args.max_price) params.set("max_price", args.max_price);
        params.set("limit", args.limit || 20);
        result = await callAPI(`/hdb/search?${params}`);
        break;

      default:
        throw new Error(`Unknown tool: ${name}`);
    }

    return {
      content: [{ type: "text", text: JSON.stringify(result, null, 2) }]
    };
  } catch (err) {
    return {
      content: [{ type: "text", text: `Error: ${err.message}` }],
      isError: true
    };
  }
});

// Start server
const transport = new StdioServerTransport();
await server.connect(transport);
console.error("asia-data-mcp v1.0.0 running on stdio");
console.error(`API base: ${API_BASE}`);
