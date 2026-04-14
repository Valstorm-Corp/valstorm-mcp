# Valstorm Model Context Protocol (MCP) Server

This directory contains a Python-based Model Context Protocol (MCP) server that uses `stdio` for communication. MCP allows you to expose structured information and tools from your project, which can then be used by AI models like Gemini to get real-time context and perform actions.

This server exposes several tools to query project information, such as its name, version, description, and status.

## Getting Started

### 1. Setup and Installation

This project uses `uv` for package management. To install the dependencies, run the `entry.sh` script from the `valstorm-mcp` directory. This will also activate the virtual environment:

```bash
source entry.sh
```

## Connecting a Client

This MCP server uses `stdio` (standard input/output) for communication. This means it's designed to be launched and managed by an MCP client application (like an IDE plugin or a desktop application), rather than being run as a standalone, long-running network server.

The client application is responsible for spawning the server process and communicating with it over `stdin` and `stdout`.

### Example: Configuring Claude for Desktop

As an example, to connect this server to Claude for Desktop, you would edit its configuration file (`claude_desktop_config.json`) to tell it how to launch the server.

The configuration would look something like this:

```json
{
  "mcpServers": {
    "valstorm-mcp": {
      "command": "uv",
      "args": [
        "run",
        "python",
        "mcp.py"
      ],
      "cwd": "/ABSOLUTE/PATH/TO/monorepo/apps/valstorm-mcp"
    }
  }
}
```

**Note:** You must replace `/ABSOLUTE/PATH/TO/monorepo/apps/valstorm-mcp` with the actual absolute path to this directory on your system.

### Using with Gemini

To use this server with Gemini, you will need to consult the Gemini client's documentation on how to configure it to connect to a local MCP server that uses `stdio` transport. The configuration will likely be similar to the example above, where you specify a command to launch the server process.
