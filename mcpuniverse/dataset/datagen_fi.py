import json
import os
from pathlib import Path
from langchain_groq import ChatGroq
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import JsonOutputParser

# Initialize the Groq Teacher Model via LangChain
# We enforce JSON output at the API level for reliability
llm = ChatGroq(
    model="llama-3.3-70b-versatile",
    api_key='',
    temperature=0.1,
    max_retries=2,
    model_kwargs={"response_format": {"type": "json_object"}}
)

# Configuration
BENCHMARK_DIR = "mcpuniverse/benchmark/configs/mcpuniverse/financial_analysis"
OUTPUT_JSONL = "mcpuniverse/dataset/finance_data.jsonl"

# Define the Prompt Template
prompt_template = ChatPromptTemplate.from_messages([
    ("system", "You are an expert data generator creating training data for an AI agent. Return ONLY a valid JSON object with a single 'messages' key containing the conversation array."),
    ("human", """The agent has access to the following MCP tool servers: {servers}.

User Question: {question}

Expected Final Output Format:
{expected_format}

Create a realistic conversation trajectory where the assistant:
1. Reasons about the required steps inside <think> tags.
2. Makes a tool call to the appropriate server using the correct parameters.
3. Receives a simulated response from the tool.
4. Provides the final answer exactly matching the Expected Final Output Format.

The returned JSON must have this exact structure:
{{
  "messages": [
    {{"role": "user", "content": "..."}},
    {{"role": "assistant", "content": "<think>...</think>\\n\\n<tool_call>...</tool_call>"}},
    {{"role": "tool", "name": "...", "content": "..."}},
    {{"role": "assistant", "content": "..."}}
  ]
}}
""")
])

# Create the LangChain processing chain
chain = prompt_template | llm | JsonOutputParser()

def generate_trajectory(task_data):
    """Prompts Llama 3.3 70B via Groq to generate a full MCP tool-calling trajectory."""
    
    question = task_data.get("question", "")
    servers = [s["name"] for s in task_data.get("mcp_servers", [])]
    expected_format = json.dumps(task_data.get("output_format", {}), indent=2)

    try:
        # Invoke the LangChain process
        trajectory = chain.invoke({
            "servers": servers,
            "question": question,
            "expected_format": expected_format
        })
        
        # Add the system prompt required for fine-tuning
        system_prompt = f"You are a helpful assistant with access to these tools: {', '.join(servers)}. Think before you act using <think> tags."
        
        messages = trajectory.get("messages", [])
        
        return {
            "messages": [{"role": "system", "content": system_prompt}] + messages
        }

    except Exception as e:
        print(f"Error generating trajectory: {e}")
        return None

def main():
    benchmark_path = Path(BENCHMARK_DIR)
    json_files = list(benchmark_path.glob("*.json"))
    print(f"Found {len(json_files)} benchmark files.")
    
    synthetic_dataset = []
    
    for file_path in json_files:
        print(f"Processing {file_path.name}...")
        with open(file_path, "r", encoding="utf-8") as f:
            task_data = json.load(f)
            
        trajectory = generate_trajectory(task_data)
        
        if trajectory:
            synthetic_dataset.append(trajectory)
            
    # Write mapped trajectories to JSONL
    with open(OUTPUT_JSONL, "w", encoding="utf-8") as f:
        for item in synthetic_dataset:
            f.write(json.dumps(item) + "\n")
            
    print(f"Successfully generated {len(synthetic_dataset)} trajectories in {OUTPUT_JSONL}")

if __name__ == "__main__":
    main()