"""
Bounty x402 Payment Test — makes a REAL paid API call.

This script:
1. Loads your burner wallet from .env
2. Calls a paid endpoint on bountyapi.com
3. Receives 402 Payment Required
4. Signs and sends USDC payment on Base
5. Receives the actual data

Cost: ~$0.01 (HDB towns endpoint)
"""

import asyncio
import os
import sys
from dotenv import load_dotenv
from eth_account import Account

from x402 import x402Client
from x402.http import x402HTTPClient
from x402.http.clients import x402HttpxClient
from x402.mechanisms.evm import EthAccountSigner
from x402.mechanisms.evm.exact.register import register_exact_evm_client

load_dotenv()


async def main():
    private_key = os.getenv("EVM_PRIVATE_KEY")
    base_url = os.getenv("BOUNTY_BASE_URL", "https://bountyapi.com")
    endpoint = "/hdb/towns"

    if not private_key or not private_key.startswith("0x"):
        print("ERROR: EVM_PRIVATE_KEY not set or invalid in .env")
        sys.exit(1)

    # Create wallet from private key
    account = Account.from_key(private_key)
    print(f"Wallet address: {account.address}")
    print(f"Target: {base_url}{endpoint}")
    print(f"Expected cost: $0.01 USDC on Base")
    print()

    # Set up x402 client
    client = x402Client()
    register_exact_evm_client(client, EthAccountSigner(account))

    http_client = x402HTTPClient(client)
    url = f"{base_url}{endpoint}"

    print("Calling paid endpoint...")
    print("---")

    async with x402HttpxClient(client) as http:
        response = await http.get(url)
        await response.aread()

        print(f"Status: {response.status_code}")
        print(f"Body: {response.text[:500]}")
        print()

        # Extract payment response
        try:
            settle = http_client.get_payment_settle_response(
                lambda name: response.headers.get(name)
            )
            print("PAYMENT SUCCESSFUL!")
            print(f"Settlement: {settle.model_dump_json(indent=2)}")
        except ValueError:
            print("No payment response header found")
            print("This means either:")
            print("  - The endpoint is free (no payment needed)")
            print("  - Payment failed")
            print(f"Headers: {dict(response.headers)}")


if __name__ == "__main__":
    asyncio.run(main())
