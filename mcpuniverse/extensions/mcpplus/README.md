# MCPPlus

> Intelligent post-processing for MCP tool outputs. Reduce token costs by up to 70%.

[![MCP-Universe](https://img.shields.io/badge/MCP-Universe-blue)]()
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)]()

## 🎯 What is MCPPlus?

MCPPlus is a post-processing extension for MCP-Universe that intelligently filters and compresses tool outputs before they reach your LLM, dramatically reducing token costs without sacrificing quality.

### The Problem

MCP tools often return large, verbose outputs (web pages, API responses, file contents). Sending these directly to your LLM:
- 💸 **Costs money** - Large context windows are expensive
- ⏱️ **Slows responses** - More tokens = slower generation
- 🧠 **Wastes context** - LLM sees irrelevant data

### The Solution

MCPPlus wraps your MCP clients and uses a **dual post-processing agent** to:
1. **Direct Extraction** - LLM extracts key information directly as text
2. **Code Generation** - LLM generates Python code to filter/parse data
3. **Best of Both** - Returns both methods for robustness

**Result:** 50-70% token reduction with minimal quality loss.

---

## 🚀 Quick Start

### Installation

```bash
pip install mcpuniverse
```

### Basic Usage

```python
from mcpuniverse.extensions.mcpplus.wrapper import MCPWrapperManager, WrapperConfig

# Create wrapper config
wrapper_config = WrapperConfig(
    enabled=True,
    token_threshold=2000,
    max_iterations=3
)

# Initialize manager
manager = MCPWrapperManager(
    config="path/to/server_config.json",
    wrapper_config=wrapper_config
)

# Your LLM
from mcpuniverse.llm import LLM
llm = LLM(config={"model_name": "gpt-4"})
manager.set_llm(llm)

# Build wrapped client
client = await manager.build_client("your-mcp-server")

# Use normally - post-processing happens automatically!
result = await client.execute_tool(
    tool_name="fetch_webpage",
    arguments={
        "url": "https://example.com",
        "expected_info": "Extract the main article title and publish date"
    }
)
```

---

## 📖 Features

### Dual Post-Processing Agent

Generates **two** extraction methods in a single LLM call:

```python
# Direct extraction output:
"Title: 'Introduction to MCP'
Published: 2024-02-05"

# Code-based output:
{
  "title": "Introduction to MCP",
  "date": "2024-02-05"
}
```

### Transparent Wrapping

Drop-in replacement for standard MCP clients:

```python
# Before (standard MCP)
client = await manager.build_client("server")

# After (with MCPPlus)
manager = MCPWrapperManager(..., wrapper_config=config)
client = await manager.build_client("server")  # Same API!
```

### Intelligent Filtering

- **Token-aware** - Only processes outputs above threshold
- **Iterative refinement** - Retries if output is empty or too large
- **Safe execution** - Sandboxed code execution with timeout
- **Fallback handling** - Returns original output on failure

---

## ⚙️ Configuration

### WrapperConfig

```python
WrapperConfig(
    enabled: bool = False,                      # Enable wrapper
    token_threshold: int = 2000,                # Min tokens to trigger processing
    post_process_llm: Optional[Dict] = None,    # Separate LLM for post-processing
    execution_timeout: int = 10,                # Code execution timeout (seconds)
    max_iterations: int = 3,                    # Max refinement iterations
    skip_iteration_on_size_failure: bool = False  # Return original if both outputs too large
)
```

### Expected Info Parameter

MCPPlus adds an `expected_info` parameter to all tools:

```python
result = await client.execute_tool(
    tool_name="search_web",
    arguments={
        "query": "Python tutorials",
        "expected_info": "List of tutorial URLs and their titles, needed to visit each page"
    }
)
```

**Guidelines for `expected_info`:**
1. **WHAT** - Specify exact data needed (e.g., "the adult ticket price", "list of URLs")
2. **WHY** - Explain how you'll use it (e.g., "to answer user's question", "to visit in next step")
3. **CONSTRAINTS** - Add limits (e.g., "maximum 10 items", "only from pricing section")

**Examples:**

✅ **Good:**
- "The adult ticket price for Universal Studios from the pricing table, needed to answer the user's question about ticket cost"
- "URLs of all product links on the page, needed to visit each product page in subsequent steps"
- "All information is needed because I need the complete page structure to locate the navigation menu"

❌ **Bad:**
- "get information" (too vague)
- "price" (unclear which price, why needed, from where)
- "check the page" (not specific about what to extract)

---

## 🏗️ Architecture

```
┌─────────────┐
│   Agent     │
│   (GPT-4)   │
└──────┬──────┘
       │ Uses tool with expected_info
       ▼
┌─────────────────────────────────┐
│  MCPWrapperManager              │
│  ┌───────────────────────────┐ │
│  │ WrappedMCPClient          │ │
│  │  ├─ Call original tool    │ │
│  │  ├─ Check token threshold │ │
│  │  └─ Post-process if needed│ │
│  └───────────────────────────┘ │
└───────────────┬─────────────────┘
                │ Large output
                ▼
┌─────────────────────────────────┐
│  PostProcessAgent               │
│  ├─ Generate direct extraction  │
│  ├─ Generate filtering code     │
│  └─ Execute & return both       │
└───────────────┬─────────────────┘
                │ Filtered output
                ▼
┌─────────────┐
│   Agent     │ <- Receives concise result
└─────────────┘
```

### Components

**PostProcessAgent** (`agent/react_postprocess_agent.py`)
- Receives tool output and expected information
- Generates both direct extraction and Python code in one LLM call
- Executes code safely and returns formatted results
- Iterates if outputs are empty or too large

**MCPWrapperManager** (`wrapper/wrapper_manager.py`)
- Extends MCPManager from MCP-Universe
- Builds wrapped MCP clients with post-processing
- Manages LLM instances and configuration

**WrappedMCPClient** (`wrapper/wrapper_manager.py`)
- Wraps standard MCPClient
- Intercepts tool calls and extracts `expected_info`
- Triggers post-processing for large outputs
- Tracks statistics (tokens saved, iterations, etc.)

**SafeCodeExecutor** (`utils/safe_executor.py`)
- Sandboxed Python code execution
- Blacklist-based security (blocks eval, exec, os operations)
- Timeout protection
- Error handling with detailed context

---

## 📁 Project Structure

```
mcpuniverse/extensions/mcpplus/
├── agent/                          # Post-processing agent
│   ├── __init__.py
│   └── react_postprocess_agent.py # Dual extraction agent
├── wrapper/                        # MCP client wrapper
│   ├── __init__.py
│   └── wrapper_manager.py          # Wrapper manager & client
├── benchmark/                      # Benchmark integration
│   ├── runner.py                   # Enhanced benchmark runner
│   ├── builder.py                  # Builder utilities
│   └── report.py                   # Report generation
├── utils/                          # Utilities
│   ├── __init__.py
│   ├── safe_executor.py            # Code execution sandbox
│   ├── stats.py                    # Token counting & stats
│   ├── tracer_analyzer.py          # Trace analysis
│   └── tracking_llm.py             # LLM usage tracking
├── examples/                       # Usage examples
│   ├── README.md
│   ├── basic_wrapper.py
│   ├── benchmark_integration.py
│   └── configs/
└── README.md                       # This file
```

---

## 📚 Examples

See [examples/](examples/) for complete examples:

- **[basic_wrapper.py](examples/basic_wrapper.py)** - Simple wrapper usage
- **[benchmark_integration.py](examples/benchmark_integration.py)** - Use with MCP-Universe benchmarks
- **[configs/simple_wrapper.yaml](examples/configs/simple_wrapper.yaml)** - Minimal configuration
- **[configs/benchmark_with_wrapper.yaml](examples/configs/benchmark_with_wrapper.yaml)** - Full benchmark config

---

## 🧪 Testing

Run tests:

```bash
# All MCPPlus tests
pytest tests/extensions/mcpplus/

# Specific component
pytest tests/extensions/mcpplus/agent/
pytest tests/extensions/mcpplus/wrapper/
pytest tests/extensions/mcpplus/utils/
```

---

## 📊 Benchmarking with MCPPlus

MCPPlus integrates seamlessly with MCP-Universe benchmarks. Add wrapper configuration to your benchmark YAML:

```yaml
kind: wrapper
spec:
  enabled: true
  token_threshold: 2000
  max_iterations: 3
  execution_timeout: 10
  skip_iteration_on_size_failure: false

---
kind: llm
spec:
  name: llm-1
  type: openai
  config:
    model_name: gpt-4

---
kind: agent
spec:
  name: ReAct-agent
  type: react
  config:
    llm: llm-1
    instruction: You are a helpful assistant.
    servers:
      - name: your-mcp-server

---
kind: benchmark
spec:
  description: Test with MCPPlus post-processing
  agent: ReAct-agent
  tasks:
    - path/to/tasks/
```

The benchmark runner will automatically:
- Initialize MCPWrapperManager with your config
- Track post-processing statistics
- Include token savings in reports

---

## 🔬 How It Works: Detailed Walkthrough

### 1. Agent Makes Tool Call

```python
result = await client.execute_tool(
    tool_name="fetch_webpage",
    arguments={
        "url": "https://example.com/article",
        "expected_info": "Extract article title and publish date"
    }
)
```

### 2. WrappedMCPClient Intercepts

- Extracts `expected_info` parameter
- Calls original MCP tool
- Receives large HTML output (e.g., 50,000 characters)

### 3. Token Check

- Counts tokens in output (e.g., 12,000 tokens)
- Compares to `token_threshold` (2,000)
- If above threshold → trigger post-processing

### 4. PostProcessAgent Processes

Sends to LLM:
```
Tool: fetch_webpage
Output: [50,000 chars of HTML]
Agent's Goal: Extract article title and publish date

Provide BOTH extraction methods in JSON:
{
  "direct_extraction": "<text>",
  "code": "<python code>"
}
```

LLM Response:
```json
{
  "direct_extraction": "Title: 'Getting Started with MCP'\nPublished: 2024-02-05",
  "code": "import re\nhtml_content = data\ntitle = re.search(r'<h1>(.*?)</h1>', html_content).group(1)\ndate = re.search(r'Published: ([\\d-]+)', html_content).group(1)\nresult = f'Title: {title}\\nDate: {date}'"
}
```

### 5. Code Execution

- SafeCodeExecutor runs the generated code
- Returns: `"Title: Getting Started with MCP\nDate: 2024-02-05"`

### 6. Format & Return

```
================================================================================
DUAL EXTRACTION RESULTS
================================================================================

Two extraction methods were used. You can use either result,
or combine information from both as appropriate.

--------------------------------------------------------------------------------
DIRECT EXTRACTION:
--------------------------------------------------------------------------------
Title: 'Getting Started with MCP'
Published: 2024-02-05

--------------------------------------------------------------------------------
CODE-BASED EXTRACTION:
--------------------------------------------------------------------------------
Title: Getting Started with MCP
Date: 2024-02-05

================================================================================
```

**Token Savings:** 12,000 → 150 tokens (98.75% reduction!)

---

## 🤝 Contributing

MCPPlus is part of MCP-Universe. Contributions welcome!

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/amazing-feature`)
3. Commit your changes (`git commit -m 'Add amazing feature'`)
4. Push to the branch (`git push origin feature/amazing-feature`)
5. Open a Pull Request

---

## 📄 License

[Add license info]

---

## 🔗 Links

- [MCP-Universe Main Repository](../../../)
- [MCP-Universe Documentation](../../../README.md)
- [MCP Specification](https://spec.modelcontextprotocol.io)
- [Research Paper](https://arxiv.org/abs/2508.14704)

---

## 💡 Tips & Best Practices

### Writing Good `expected_info`

The quality of post-processing depends heavily on clear `expected_info` descriptions:

**Structure your description:**
1. Start with what you need: "Extract X"
2. Add context: "from Y section"
3. Explain purpose: "needed to Z"
4. Add constraints: "maximum N items"

**Example:**
```
"Extract the top 5 product names and prices from the search results table,
needed to compare options and make a purchasing decision"
```

### When to Use MCPPlus

✅ **Use when:**
- Tool outputs are large (>2000 tokens)
- You need specific information from verbose responses
- Token costs are a concern
- You're working with structured data (HTML, JSON, XML)

❌ **Skip when:**
- Tool outputs are already concise (<500 tokens)
- You need ALL information from the output
- Processing overhead exceeds token savings
- Real-time performance is critical

### Monitoring Performance

Track post-processing statistics:

```python
# Get aggregated stats
stats = manager.get_all_postprocessor_stats()

print(f"Tool calls processed: {stats['tool_calls_processed']}")
print(f"Total tokens saved: {stats['total_tokens_reduced']}")
print(f"Total iterations: {stats['total_iterations']}")
print(f"Average compression: {stats['tokens_reduced'] / stats['original_tokens'] * 100:.1f}%")
```

---

## ❓ FAQ

**Q: Does MCPPlus work with all MCP servers?**
A: Yes! MCPPlus wraps the MCP client layer, so it works with any MCP server regardless of implementation.

**Q: What if post-processing fails?**
A: MCPPlus always falls back to the original tool output. Your agent never receives an error due to post-processing failure.

**Q: Can I use a different LLM for post-processing?**
A: Yes! Set `post_process_llm` in WrapperConfig to use a separate (potentially cheaper/faster) model for filtering.

**Q: How much does post-processing cost?**
A: Each post-processing call uses ~1000-2000 tokens (prompt + response). However, if you save 10,000+ tokens on the main agent call, the net savings are substantial.

**Q: Does it work with streaming responses?**
A: Currently, MCPPlus processes complete tool outputs. Streaming support is planned for future releases.

