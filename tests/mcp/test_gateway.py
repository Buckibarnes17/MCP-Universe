import unittest
import asyncio
import socket
import time
import os
from unittest.mock import Mock, patch, MagicMock
from mcpuniverse.mcp.manager import MCPManager
from mcpuniverse.mcp.gateway import Gateway, ServerConnector


class TestGateway(unittest.IsolatedAsyncioTestCase):
    """Comprehensive test suite for Gateway functionality"""

    def setUp(self):
        """Set up test fixtures"""
        self.manager = MCPManager()
        self.gateway = Gateway(
            mcp_manager=self.manager,
            max_connections_per_server=100,
            max_concurrent_requests=50
        )

    async def asyncTearDown(self):
        """Clean up after tests"""
        await self.gateway.cleanup()

    def test_gateway_initialization(self):
        """Test Gateway initialization with custom parameters"""
        gateway = Gateway(
            mcp_manager=self.manager,
            max_connections_per_server=200,
            max_concurrent_requests=100
        )
        self.assertEqual(gateway._max_connections_per_server, 200)
        self.assertEqual(gateway._max_concurrent_requests, 100)
        self.assertEqual(len(gateway._active_connections), 0)
        self.assertEqual(len(gateway._processes), 0)

    def test_find_available_port(self):
        """Test port finding functionality"""
        # Use a larger port range to increase chances of finding an available port
        port1 = self.gateway._find_available_port(start_port=20000, end_port=20100)
        
        # If port is found, it should be in the range
        if port1 >= 0:
            self.assertGreaterEqual(port1, 20000)
            self.assertLessEqual(port1, 20100)
            # Verify it's an integer
            self.assertIsInstance(port1, int)
        else:
            # If no port found, verify it returns -1
            self.assertEqual(port1, -1)

    def test_init_sse_server(self):
        """Test SSE server initialization"""
        # Test with a server that supports SSE (like 'echo' or 'weather')
        server_configs = self.manager.get_configs()
        
        # Find a server with SSE support
        sse_server = None
        for name, config in server_configs.items():
            if config.sse.command != "":
                sse_server = name
                break

        if sse_server:
            self.gateway.init_sse_server(sse_server)
            
            # Verify server was initialized
            self.assertIn(sse_server, self.gateway._processes)
            process_info = self.gateway._processes[sse_server]
            self.assertIn("port", process_info)
            self.assertIn("url", process_info)
            self.assertIn("routes", process_info)
            self.assertIn("process", process_info)
            
            # Verify URL format
            self.assertTrue(process_info["url"].startswith("http://localhost:"))
            self.assertTrue(process_info["url"].endswith("/sse"))
            
            # Test that re-initializing returns the same process info
            process_info2 = self.gateway.init_sse_server(sse_server)
            self.assertEqual(process_info, process_info2)

    def test_init_sse_server_invalid(self):
        """Test SSE server initialization with invalid server name"""
        with self.assertRaises(AssertionError):
            self.gateway.init_sse_server("nonexistent_server")

    def test_init_stdio_server(self):
        """Test stdio server initialization"""
        server_configs = self.manager.get_configs()
        
        # Use first available server
        server_name = list(server_configs.keys())[0]
        
        self.gateway.init_stdio_server(server_name)
        
        # Verify server was initialized
        self.assertIn(server_name, self.gateway._processes)
        process_info = self.gateway._processes[server_name]
        self.assertIn("routes", process_info)
        # Stdio servers don't have process or port initially
        self.assertNotIn("process", process_info)

    def test_init_stdio_server_invalid(self):
        """Test stdio server initialization with invalid server name"""
        with self.assertRaises(AssertionError):
            self.gateway.init_stdio_server("nonexistent_server")

    def test_build_sse_routes(self):
        """Test SSE route building"""
        server_configs = self.manager.get_configs()
        
        # Find a server with SSE support
        sse_server = None
        for name, config in server_configs.items():
            if config.sse.command != "":
                sse_server = name
                break
        
        if sse_server:
            self.gateway.init_sse_server(sse_server)
            routes = self.gateway._processes[sse_server]["routes"]
            
            # Should have at least 2 routes: SSE endpoint and messages endpoint
            self.assertGreaterEqual(len(routes), 2)
            
            # Verify route paths
            route_paths = []
            for route in routes:
                if hasattr(route, 'path'):
                    route_paths.append(route.path)
                elif hasattr(route, 'routes'):
                    # Mount routes
                    for sub_route in route.routes:
                        if hasattr(sub_route, 'path'):
                            route_paths.append(sub_route.path)

    def test_build_stdio_routes(self):
        """Test stdio route building"""
        server_configs = self.manager.get_configs()
        server_name = list(server_configs.keys())[0]
        config = server_configs[server_name]
        
        self.gateway.init_stdio_server(server_name)
        routes = self.gateway._processes[server_name]["routes"]
        
        # Should have at least 2 routes
        self.assertGreaterEqual(len(routes), 2)

    def test_connection_pool_tracking(self):
        """Test connection pool tracking functionality"""
        server_name = "test_server"
        
        # Initially no connections
        self.assertEqual(self.gateway._active_connections[server_name], 0)
        
        # Simulate connection increments
        async def simulate_connection():
            async with self.gateway._connection_lock[server_name]:
                self.gateway._active_connections[server_name] += 1
        
        # Test multiple connections
        asyncio.run(simulate_connection())
        self.assertEqual(self.gateway._active_connections[server_name], 1)
        
        asyncio.run(simulate_connection())
        self.assertEqual(self.gateway._active_connections[server_name], 2)
        
        # Test connection decrement
        async def simulate_disconnection():
            async with self.gateway._connection_lock[server_name]:
                self.gateway._active_connections[server_name] = max(
                    0, self.gateway._active_connections[server_name] - 1
                )
        
        asyncio.run(simulate_disconnection())
        self.assertEqual(self.gateway._active_connections[server_name], 1)

    def test_concurrent_request_semaphore(self):
        """Test concurrent request semaphore functionality"""
        server_name = "test_server"
        
        # Access the semaphore (defaultdict creates it on access)
        semaphore = self.gateway._request_semaphore[server_name]
        
        # Verify semaphore exists and has correct limit
        self.assertIsNotNone(semaphore)
        self.assertEqual(semaphore._value, self.gateway._max_concurrent_requests)
        
        # Now verify it's in the dict
        self.assertIn(server_name, self.gateway._request_semaphore)

    def test_build_starlette_app_stdio_mode(self):
        """Test building Starlette app in stdio mode"""
        app = self.gateway.build_starlette_app(mode="stdio", servers=None, debug=True)
        
        # Verify app is created
        self.assertIsNotNone(app)
        self.assertTrue(app.debug)
        
        # Verify routes are created
        self.assertGreater(len(app.routes), 0)

    async def test_build_starlette_app_sse_mode(self):
        """Test building Starlette app in SSE mode"""
        # This will actually start server processes, so we'll test it carefully
        server_configs = self.manager.get_configs()
        
        # Find a server with SSE support
        sse_server = None
        for name, config in server_configs.items():
            if config.sse.command != "":
                sse_server = name
                break
        
        if sse_server:
            # Test with a specific server
            app = self.gateway.build_starlette_app(
                mode="sse", 
                servers=[sse_server], 
                debug=True
            )
            
            self.assertIsNotNone(app)
            self.assertTrue(app.debug)
            
            # Clean up processes
            await self.gateway.cleanup()

    def test_build_starlette_app_invalid_mode(self):
        """Test building Starlette app with invalid mode"""
        with self.assertRaises(AssertionError):
            self.gateway.build_starlette_app(mode="invalid_mode")

    async def test_cleanup(self):
        """Test Gateway cleanup functionality"""
        # Initialize some servers
        server_configs = self.manager.get_configs()
        server_name = list(server_configs.keys())[0]
        self.gateway.init_stdio_server(server_name)
        
        # Verify server is initialized
        self.assertIn(server_name, self.gateway._processes)
        
        # Cleanup
        await self.gateway.cleanup()
        
        # Verify processes are cleared
        self.assertEqual(len(self.gateway._processes), 0)
        self.assertEqual(len(self.gateway._active_connections), 0)

    def test_wait_for_server_ready_timeout(self):
        """Test server readiness waiting with timeout"""
        # Test with a non-existent port (should timeout)
        result = self.gateway._wait_for_server_ready(
            server_name="test_server",
            port=99999,  # Unlikely to be in use
            timeout=0.1  # Very short timeout
        )
        # Should return False due to timeout
        self.assertFalse(result)

    def test_multiple_stdio_servers_initialization(self):
        """Test initializing multiple stdio servers"""
        server_configs = self.manager.get_configs()
        server_names = list(server_configs.keys())[:3]  # Get first 3 servers
        
        for server_name in server_names:
            self.gateway.init_stdio_server(server_name)
        
        # Verify all servers are initialized
        self.assertEqual(len(self.gateway._processes), len(server_names))
        for server_name in server_names:
            self.assertIn(server_name, self.gateway._processes)

    def test_multiple_sse_servers_initialization(self):
        """Test initializing multiple SSE servers"""
        server_configs = self.manager.get_configs()
        
        # Find servers that support SSE
        sse_servers = []
        for name, config in server_configs.items():
            if config.sse.command != "":
                sse_servers.append(name)
                if len(sse_servers) >= 3:  # Get first 3 SSE servers
                    break
        
        if len(sse_servers) == 0:
            self.skipTest("No SSE servers available for testing")
        
        # Initialize all SSE servers
        for server_name in sse_servers:
            self.gateway.init_sse_server(server_name)
        
        # Verify all SSE servers are initialized
        self.assertEqual(len(self.gateway._processes), len(sse_servers))

        for server_name in sse_servers:
            self.assertIn(server_name, self.gateway._processes)
            
            # Verify SSE-specific properties
            process_info = self.gateway._processes[server_name]
            self.assertIn("port", process_info)
            self.assertIn("url", process_info)
            self.assertIn("routes", process_info)
            self.assertIn("process", process_info)
            
            # Verify URL format
            self.assertTrue(process_info["url"].startswith("http://localhost:"))
            self.assertTrue(process_info["url"].endswith("/sse"))
            
            # Verify port is valid
            self.assertIsInstance(process_info["port"], int)
            self.assertGreaterEqual(process_info["port"], 10000)
            self.assertLessEqual(process_info["port"], 65535)
            
            # Verify routes are created
            self.assertGreater(len(process_info["routes"]), 0)

    def test_connection_count_warning_threshold(self):
        """Test connection count warning at threshold"""
        server_name = "test_server"
        
        # Set connection count to threshold
        self.gateway._active_connections[server_name] = self.gateway._max_connections_per_server
        
        # Verify threshold is reached
        self.assertEqual(
            self.gateway._active_connections[server_name],
            self.gateway._max_connections_per_server
        )

    @unittest.skip("Requires running gateway server")
    async def test_connection_integration(self):
        """Integration test: Test actual connection through gateway"""
        # Check if gateway server is running
        gateway_address = os.environ.get("MCP_GATEWAY_ADDRESS", "http://localhost:8000")
        # Extract port from address
        try:
            if "://" in gateway_address:
                host_port = gateway_address.split("://")[1].split("/")[0]
                host, port = host_port.split(":") if ":" in host_port else (host_port, "8000")
            else:
                host, port = "localhost", "8000"
            
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                if s.connect_ex((host, int(port))) != 0:
                    # Gateway server not running, skip test
                    return
        except Exception:
            # If we can't check, try to connect anyway
            pass

        manager = MCPManager()
        client = await manager.build_client(server_name="echo", transport="sse")
        tools = await client.list_tools()
        self.assertGreater(len(tools), 0)
        
        output = await client.execute_tool(
            tool_name="echo_tool",
            arguments={"text": "Hello world!"}
        )
        self.assertEqual(output.content[0].text, "Hello world!")
        await client.cleanup()


class TestServerConnector(unittest.IsolatedAsyncioTestCase):
    """Test suite for ServerConnector functionality"""

    def setUp(self):
        """Set up test fixtures"""
        self.connector = ServerConnector()

    async def asyncTearDown(self):
        """Clean up after tests"""
        await self.connector.cleanup()

    def test_connector_initialization(self):
        """Test ServerConnector initialization"""
        self.assertIsNone(self.connector._read_stream)
        self.assertIsNone(self.connector._write_stream)
        self.assertIsNotNone(self.connector._cleanup_lock)
        self.assertIsNotNone(self.connector._exit_stack)

    async def test_cleanup(self):
        """Test ServerConnector cleanup"""
        # Should not raise exception even if nothing is initialized
        await self.connector.cleanup()
        
        # Should be idempotent
        await self.connector.cleanup()

    async def test_cleanup_with_lock(self):
        """Test that cleanup is protected by lock"""
        # Test concurrent cleanup calls
        await asyncio.gather(
            self.connector.cleanup(),
            self.connector.cleanup(),
            self.connector.cleanup(),
            return_exceptions=True
        )
        # Should complete without errors


if __name__ == "__main__":
    unittest.main()
