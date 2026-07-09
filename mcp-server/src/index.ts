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

// ============================================================
// Anonymous install ping — no user data, just version + client
// ============================================================

try {
  const pingUrl = `${API_BASE}/ping?version=1.5.0&client=${encodeURIComponent(process.env.MCP_CLIENT_NAME || "unknown")}`;
  fetch(pingUrl).catch(() => {}); // fire and forget, never block startup
} catch {
  // ping failure should never affect functionality
}

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
    name: "sg_address_intel",
    description: `Full address intelligence for a Singapore postal code. Returns district, planning area (URA Master Plan), market region (CCR/RCR/OCR), HDB town, approximate coordinates, and the 5 nearest MRT stations with walking distance and time. 140+ MRT stations in database covering all 6 lines. ${FREE_BADGE}`,
    inputSchema: {
      type: "object",
      properties: {
        postal_code: { type: "string", description: "Singapore postal code (6 digits), e.g. 238582" }
      },
      required: ["postal_code"]
    }
  },
  {
    name: "sg_mrt_near",
    description: `Find nearest MRT/LRT stations to a Singapore postal code. Returns station name, MRT lines, distance in km, and estimated walking time. Free endpoint. ${FREE_BADGE}`,
    inputSchema: {
      type: "object",
      properties: {
        postal_code: { type: "string", description: "Singapore postal code (6 digits)" },
        limit: { type: "integer", description: "Max results (default 5, max 20)", default: 5 }
      },
      required: ["postal_code"]
    }
  },
  {
    name: "sg_mrt_search",
    description: `Search MRT stations by name. Returns station name, MRT line codes, and coordinates. Covers all 140+ stations across 6 MRT lines. ${FREE_BADGE}`,
    inputSchema: {
      type: "object",
      properties: {
        q: { type: "string", description: "Station name to search (partial match), e.g. 'Bishan' or 'Tamp'" },
        limit: { type: "integer", description: "Max results (default 10)", default: 10 }
      },
      required: ["q"]
    }
  },
  {
    name: "sg_affordability",
    description: `Calculate Singapore property loan affordability under MAS TDSR/MSR framework. Checks if a borrower can afford a property based on Total Debt Servicing Ratio (55%), Mortgage Servicing Ratio (30% for HDB), LTV limits, and stress-tested interest rates. Returns max affordable loan and property price. ${PAID_BADGE}`,
    inputSchema: {
      type: "object",
      properties: {
        monthly_income: { type: "number", description: "Gross monthly income in SGD (all borrowers combined)" },
        property_price: { type: "number", description: "Property purchase price in SGD" },
        loan_type: { type: "string", enum: ["hdb", "bank_hdb", "bank_private"], default: "bank_private", description: "hdb=HDB loan, bank_hdb=bank loan for HDB, bank_private=bank loan for private property" },
        existing_monthly_debt: { type: "number", description: "Total existing monthly debt obligations (car loans, credit cards, other mortgages)", default: 0 },
        loan_tenure_years: { type: "integer", description: "Loan tenure in years (max 30 HDB, 35 private)", default: 30 },
        borrower_age: { type: "integer", description: "Age of youngest borrower", default: 35 },
        housing_loan_count: { type: "integer", description: "Number of outstanding housing loans including this one", default: 1 }
      },
      required: ["monthly_income", "property_price"]
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
  },
  {
    name: "sg_property_analyze",
    description: `COMPLETE property investment analysis in one call. Combines stamp duty (IRAS), HDB transaction comparables (data.gov.sg), rental yield, MAS TDSR/MSR affordability check, and MRT location intelligence. Returns a verdict with risk flags. This is the most comprehensive Singapore property analysis endpoint available — no agent can replicate this by scraping. Region parameter supports future expansion (SG now, HK/AE/AU/JP planned). ${PAID_BADGE}`,
    inputSchema: {
      type: "object",
      properties: {
        property_type: { type: "string", enum: ["hdb", "private"], default: "hdb" },
        region: { type: "string", enum: ["SG", "HK", "AE", "AU", "JP"], default: "SG", description: "Region/country code. SG supported now; HK/AE/AU/JP planned." },
        property_price: { type: "number", description: "Property price / asking price in SGD" },
        town: { type: "string", description: "HDB town (e.g. 'TAMPINES'). Required for HDB analysis." },
        flat_type: { type: "string", description: "HDB flat type (e.g. '4 ROOM')" },
        postal_code: { type: "string", description: "Postal code for location intelligence (optional)" },
        monthly_rent: { type: "number", description: "Expected monthly rent in SGD (optional, for yield analysis)" },
        buyer_profile: { type: "string", enum: ["SC", "SPR", "FR", "entity"], default: "SC" },
        property_count: { type: "integer", description: "Number of properties owned including this one", default: 1 },
        monthly_income: { type: "number", description: "Gross monthly income for affordability check (optional)" },
        existing_monthly_debt: { type: "number", description: "Existing monthly debt obligations", default: 0 },
        loan_tenure_years: { type: "integer", description: "Loan tenure in years", default: 30 },
        borrower_age: { type: "integer", description: "Age of youngest borrower", default: 35 }
      },
      required: ["property_price"]
    }
  },
  {
    name: "sg_property_rank",
    description: `Rank multiple candidate properties by investment value. Accepts properties from ANY source (user, web search, listing portals) and enriches each with stamp duty, transaction comps, rental yield, affordability, and location data. Returns ranked list with transparent scores (0-100) across 4 dimensions: value vs comps, rental yield, affordability, location. This is the decision layer — an agent gathers listings anywhere, Bounty tells it which one is best. ${PAID_BADGE}`,
    inputSchema: {
      type: "object",
      properties: {
        candidates: {
          type: "array",
          description: "List of candidate properties to evaluate",
          minItems: 1,
          maxItems: 50,
          items: {
            type: "object",
            properties: {
              name: { type: "string", description: "Property name or address (optional)" },
              property_type: { type: "string", default: "hdb", description: "hdb, private, condo, landed" },
              price: { type: "number", description: "Asking price in SGD" },
              town: { type: "string", description: "Town or area (e.g. 'TAMPINES')" },
              flat_type: { type: "string", description: "HDB flat type (e.g. '4 ROOM')" },
              postal_code: { type: "string", description: "Postal code for location (optional)" },
              monthly_rent: { type: "number", description: "Expected monthly rent (optional)" }
            },
            required: ["price"]
          }
        },
        region: { type: "string", enum: ["SG", "HK", "AE", "AU", "JP"], default: "SG", description: "Region/country code. SG supported now; HK/AE/AU/JP planned." },
        buyer_profile: { type: "string", enum: ["SC", "SPR", "FR", "entity"], default: "SC" },
        monthly_income: { type: "number", description: "Gross monthly income for affordability (optional)" },
        existing_monthly_debt: { type: "number", description: "Existing monthly debt obligations", default: 0 }
      },
      required: ["candidates"]
    }
  },
  {
    name: "sg_property_pitch",
    description: `Generate a complete property investment pitch — the kind of one-page analysis a property agent presents to a client. Combines price fairness vs transaction comps, stamp duty breakdown, MAS affordability check, rental yield projection, location intelligence (MRT, district, region), tenure/lease risk assessment, and a plain-English verdict with recommendation. This is the highest-value output for property agents and investors. ${PAID_BADGE}`,
    inputSchema: {
      type: "object",
      properties: {
        property_type: { type: "string", default: "hdb", description: "hdb, private, condo, landed" },
        property_price: { type: "number", description: "Asking price in SGD" },
        town: { type: "string", description: "HDB town (e.g. 'TAMPINES') or area name" },
        flat_type: { type: "string", description: "HDB flat type (e.g. '4 ROOM') or unit type" },
        project_name: { type: "string", description: "Condo/project name (for private property)" },
        postal_code: { type: "string", description: "Postal code for location intelligence" },
        sqft: { type: "number", description: "Floor area in square feet" },
        monthly_rent: { type: "number", description: "Expected monthly rent (optional)" },
        tenure: { type: "string", description: "Freehold, 99-year, 999-year, etc." },
        top_year: { type: "integer", description: "Year of Temporary Occupation Permit (TOP)" },
        buyer_profile: { type: "string", enum: ["SC", "SPR", "FR", "entity"], default: "SC" },
        property_count: { type: "integer", default: 1 },
        monthly_income: { type: "number", description: "Gross monthly income" },
        existing_monthly_debt: { type: "number", default: 0 },
        buyer_notes: { type: "string", description: "Any specific concerns or goals" }
      },
      required: ["property_price"]
    }
  },
  {
    name: "sg_income_tax",
    description: `Calculate Singapore individual income tax. Resident progressive rates (0-22%, YA 2024+). Non-residents: 15% flat or progressive. Returns marginal breakdown, effective rate, and tax payable. FREE`,
    inputSchema: {
      type: "object",
      properties: {
        annual_income: { type: "number", description: "Gross annual income in SGD" },
        deductions: { type: "number", description: "Total deductions (CPF, expenses, donations)", default: 0 },
        reliefs: { type: "number", description: "Total personal reliefs (earned income, spouse, child)", default: 0 },
        is_resident: { type: "boolean", description: "Tax residency status", default: true }
      },
      required: ["annual_income"]
    }
  },
  {
    name: "sg_gst",
    description: `Add or remove Singapore GST (9% from 1 Jan 2024). mode='add' calculates GST on a price; mode='remove' extracts GST from a GST-inclusive price. FREE`,
    inputSchema: {
      type: "object",
      properties: {
        amount: { type: "number", description: "Amount in SGD" },
        mode: { type: "string", enum: ["add", "remove"], default: "add", description: "'add' (add GST) or 'remove' (extract GST from inclusive price)" }
      },
      required: ["amount"]
    }
  },
  {
    name: "sg_property_commission",
    description: `Estimate Singapore property agent commission for sale or rental transactions. HDB, private, landed. Rates are market norms (CEA), not legally fixed. FREE`,
    inputSchema: {
      type: "object",
      properties: {
        transaction_type: { type: "string", enum: ["sale", "rental", "sublet"], description: "Type of property transaction" },
        property_type: { type: "string", enum: ["hdb", "private", "landed"], default: "hdb" },
        price: { type: "number", description: "Sale price or monthly rent in SGD" },
        is_seller_landlord: { type: "boolean", default: true, description: "true = seller/landlord side, false = buyer/tenant side" }
      },
      required: ["transaction_type", "price"]
    }
  },
  {
    name: "sg_cpf_housing",
    description: `Estimate CPF Ordinary Account (OA) accumulation for housing use. Shows monthly OA contribution by age band, 3-year and 5-year projected balances at 2.5% interest. FREE`,
    inputSchema: {
      type: "object",
      properties: {
        monthly_income: { type: "number", description: "Gross monthly income in SGD" },
        age: { type: "integer", description: "Current age (16-65)" },
        existing_oa_balance: { type: "number", description: "Existing CPF OA balance", default: 0 }
      },
      required: ["monthly_income", "age"]
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
  { name: "bountyapi-mcp", version: "1.4.0" },
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

      case "sg_address_intel":
        result = await callAPI(`/address/${toolArgs.postal_code}`);
        break;

      case "sg_mrt_near": {
        const mrtLimit = (toolArgs.limit as number) || 5;
        result = await callAPI(`/mrt/near/${toolArgs.postal_code}?limit=${mrtLimit}`);
        break;
      }

      case "sg_mrt_search": {
        const searchLimit = (toolArgs.limit as number) || 10;
        result = await callAPI(`/mrt/search?q=${encodeURIComponent(toolArgs.q as string)}&limit=${searchLimit}`);
        break;
      }

      case "sg_affordability":
        result = await callAPI("/affordability/calculate", "POST", {
          monthly_income: toolArgs.monthly_income,
          property_price: toolArgs.property_price,
          loan_type: toolArgs.loan_type || "bank_private",
          existing_monthly_debt: toolArgs.existing_monthly_debt || 0,
          loan_tenure_years: toolArgs.loan_tenure_years || 30,
          borrower_age: toolArgs.borrower_age || 35,
          housing_loan_count: toolArgs.housing_loan_count || 1
        });
        break;

      case "sg_property_analyze":
        result = await callAPI("/property/analyze", "POST", {
          property_type: toolArgs.property_type || "hdb",
          region: toolArgs.region || "SG",
          property_price: toolArgs.property_price,
          town: toolArgs.town,
          flat_type: toolArgs.flat_type,
          postal_code: toolArgs.postal_code,
          monthly_rent: toolArgs.monthly_rent,
          buyer_profile: toolArgs.buyer_profile || "SC",
          property_count: toolArgs.property_count || 1,
          monthly_income: toolArgs.monthly_income,
          existing_monthly_debt: toolArgs.existing_monthly_debt || 0,
          loan_tenure_years: toolArgs.loan_tenure_years || 30,
          borrower_age: toolArgs.borrower_age || 35
        });
        break;

      case "sg_property_rank":
        result = await callAPI("/property/rank", "POST", {
          candidates: toolArgs.candidates,
          region: toolArgs.region || "SG",
          buyer_profile: toolArgs.buyer_profile || "SC",
          monthly_income: toolArgs.monthly_income,
          existing_monthly_debt: toolArgs.existing_monthly_debt || 0,
        });
        break;

      case "sg_property_pitch":
        result = await callAPI("/property/pitch", "POST", {
          property_type: toolArgs.property_type || "hdb",
          property_price: toolArgs.property_price,
          town: toolArgs.town,
          flat_type: toolArgs.flat_type,
          project_name: toolArgs.project_name,
          postal_code: toolArgs.postal_code,
          sqft: toolArgs.sqft,
          monthly_rent: toolArgs.monthly_rent,
          tenure: toolArgs.tenure,
          top_year: toolArgs.top_year,
          buyer_profile: toolArgs.buyer_profile || "SC",
          property_count: toolArgs.property_count || 1,
          monthly_income: toolArgs.monthly_income,
          existing_monthly_debt: toolArgs.existing_monthly_debt || 0,
          buyer_notes: toolArgs.buyer_notes,
        });
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

      case "sg_income_tax":
        result = await callAPI(`/tax/income?annual_income=${toolArgs.annual_income}&deductions=${toolArgs.deductions || 0}&reliefs=${toolArgs.reliefs || 0}&is_resident=${toolArgs.is_resident !== false}`);
        break;

      case "sg_gst":
        result = await callAPI(`/gst?amount=${toolArgs.amount}&mode=${toolArgs.mode || "add"}`);
        break;

      case "sg_property_commission":
        result = await callAPI(`/commission?transaction_type=${toolArgs.transaction_type}&property_type=${toolArgs.property_type || "hdb"}&price=${toolArgs.price}&is_seller_landlord=${toolArgs.is_seller_landlord !== false}`);
        break;

      case "sg_cpf_housing":
        result = await callAPI(`/cpf/housing?monthly_income=${toolArgs.monthly_income}&age=${toolArgs.age}&existing_oa_balance=${toolArgs.existing_oa_balance || 0}`);
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
console.error(`bountyapi-mcp v1.4.0 running on stdio`);
console.error(`API base: ${API_BASE}`);
console.error(`Payment: ${walletAddress ? "enabled" : "disabled (free endpoints only)"}`);
