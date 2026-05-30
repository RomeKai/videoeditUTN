import os
import asyncio
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from crewai.tools import BaseTool

class EngramMCPClient:
    """
    Client to connect to the Gentle-AI Engram server via MCP.
    """
    def __init__(self):
        # Engram usually runs as a local binary or node process.
        # We assume 'gentle-ai' is in the PATH or we use a configurable command.
        self.server_params = StdioServerParameters(
            command="gentle-ai",
            args=["engram", "mcp"], # Command to start the MCP server
            env=os.environ.copy()
        )

    async def _execute_mcp_command(self, tool_name: str, arguments: dict):
        """Internal helper to run an MCP tool command."""
        try:
            async with stdio_client(self.server_params) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    result = await session.call_tool(tool_name, arguments)
                    return result.content
        except Exception as e:
            return f"Error connecting to Engram MCP: {str(e)}"

class EngramTool(BaseTool):
    name: str = "engram_memory"
    description: str = "Read and write project context and architectural decisions from Engram (Gentle-AI)."

    def _run(self, action: str, query: str = "", key: str = "", value: str = ""):
        """
        Actions: 
        - 'read': Search historical decisions.
        - 'write': Save a new architectural decision.
        """
        client = EngramMCPClient()
        loop = asyncio.get_event_loop()
        
        if action == "read":
            return loop.run_until_complete(
                client._execute_mcp_command("leer_contexto_proyecto", {"query": query})
            )
        elif action == "write":
            return loop.run_until_complete(
                client._execute_mcp_command("guardar_decision_arquitectonica", {"key": key, "value": value})
            )
        return "Invalid action. Use 'read' or 'write'."
