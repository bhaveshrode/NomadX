import logging
import httpx
from typing import List, Dict, Any, Optional
from ..interfaces import IBlockchainClient

logger = logging.getLogger(__name__)

class BaseRpcClient(IBlockchainClient):
    """
    Lightweight JSON-RPC client performing standard Ethereum log and height queries.
    Features automatic failover across multi-node fallback RPC endpoints.
    """
    def __init__(
        self,
        rpc_url: str,
        fallback_urls: Optional[List[str]] = None,
        timeout_seconds: int = 10
    ) -> None:
        self._primary_url = rpc_url
        self._fallback_urls = fallback_urls or []
        self._timeout = timeout_seconds

    def _get_endpoints(self) -> List[str]:
        endpoints = [self._primary_url]
        for url in self._fallback_urls:
            if url and url not in endpoints:
                endpoints.append(url)
        return endpoints

    async def _call_rpc_with_failover(self, method: str, params: List[Any]) -> Any:
        endpoints = self._get_endpoints()
        last_exception = None

        for endpoint in endpoints:
            payload = {
                "jsonrpc": "2.0",
                "method": method,
                "params": params,
                "id": 1
            }
            try:
                async with httpx.AsyncClient(timeout=self._timeout) as client:
                    response = await client.post(endpoint, json=payload)
                    response.raise_for_status()
                    data = response.json()
                    if "error" in data:
                        raise ValueError(f"RPC Error on [{endpoint}]: {data['error']}")
                    return data["result"]
            except Exception as e:
                logger.warning(f"RPC call [{method}] failed on endpoint [{endpoint}]: {str(e)}. Retrying next endpoint...")
                last_exception = e

        raise RuntimeError(f"All RPC endpoints failed for [{method}]: {str(last_exception)}")

    async def get_latest_block_number(self) -> int:
        result = await self._call_rpc_with_failover("eth_blockNumber", [])
        return int(result, 16)

    async def get_logs(
        self,
        from_block: int,
        to_block: int,
        address: str,
        topics: List[str]
    ) -> List[Dict[str, Any]]:
        params = {
            "fromBlock": hex(from_block),
            "toBlock": hex(to_block),
            "address": address.lower()
        }
        if topics:
            params["topics"] = topics

        result = await self._call_rpc_with_failover("eth_getLogs", [params])
        return result or []

    async def get_transaction_receipt(self, tx_hash: str) -> Optional[Dict[str, Any]]:
        return await self._call_rpc_with_failover("eth_getTransactionReceipt", [tx_hash])
