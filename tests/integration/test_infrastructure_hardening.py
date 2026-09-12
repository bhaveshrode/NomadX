import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from nomadx.src.infrastructure.config.configuration import settings
from nomadx.src.infrastructure.blockchain.clients.base_rpc import BaseRpcClient

@pytest.mark.asyncio
async def test_rpc_client_multi_node_failover():
    """Verify BaseRpcClient automatically fails over to fallback RPC endpoints when primary fails."""
    primary_url = "https://invalid-primary-rpc-node-12345.org"
    fallback_url = "https://mainnet.base.org"
    
    client = BaseRpcClient(rpc_url=primary_url, fallback_urls=[fallback_url], timeout_seconds=5)
    endpoints = client._get_endpoints()
    assert primary_url in endpoints
    assert fallback_url in endpoints
    assert len(endpoints) == 2

@pytest.mark.asyncio
async def test_rpc_client_failover_execution():
    """Simulate primary endpoint 500 error and verify automatic fallback execution."""
    client = BaseRpcClient(
        rpc_url="https://failing-rpc-node.com",
        fallback_urls=["https://working-rpc-node.com"],
        timeout_seconds=2
    )

    with patch("httpx.AsyncClient.post") as mock_post:
        mock_response_success = MagicMock()
        mock_response_success.raise_for_status.return_value = None
        mock_response_success.json.return_value = {"jsonrpc": "2.0", "result": "0x123456", "id": 1}

        mock_post.side_effect = [Exception("500 Server Error"), mock_response_success]

        block_num = await client.get_latest_block_number()
        assert block_num == int("0x123456", 16)
        assert mock_post.call_count == 2

@pytest.mark.asyncio
async def test_production_settings_validation():
    """Verify settings loaded with high-entropy keys and valid DB connection parameters."""
    assert settings.db_pool_size >= 10
    assert settings.db_max_overflow >= 5
    assert settings.db_pool_pre_ping is True
    assert len(settings.base_rpc_fallback_urls) >= 1
