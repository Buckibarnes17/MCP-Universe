"""LLM module containing various language model implementations."""

from .openai import OpenAIModel
from .mistral import MistralModel
from .claude import ClaudeModel
from .ollama import OllamaModel
from .deepseek import DeepSeekModel
from .claude_gateway import ClaudeGatewayModel
from .grok import GrokModel
from .openai_agent import OpenAIAgentModel
from .openrouter import OpenRouterModel
from .gemini import GeminiModel
from .vllm_local import VLLMLocalModel
from .claude_wr import ClaudeWRModel
# TITO (Token In Token Out) — vLLM engine, trajectory manager, agent wrapper
from .tito import (
    AsyncVLLMEngine, AsyncVLLMBackend, VLLMEngineConfig,
    TokenTrajectoryManager, TokenTrajectory, TokenSegment,
    TITOLLMWrapper, TITOLLMConfig,
)

__all__ = [
    "OpenAIModel",
    "MistralModel",
    "ClaudeModel",
    "OllamaModel",
    "DeepSeekModel",
    "ClaudeGatewayModel",
    "ClaudeWRModel",
    "GrokModel",
    "OpenAIAgentModel",
    "OpenRouterModel",
    "GeminiModel",
    "VLLMLocalModel",
    # Direct vLLM engine (no HTTP serve)
    "AsyncVLLMEngine",
    "AsyncVLLMBackend",
    "VLLMEngineConfig",
    # TITO (Token In Token Out) for RL training
    "TokenTrajectoryManager",
    "TokenTrajectory",
    "TokenSegment",
    "TITOLLMWrapper",
    "TITOLLMConfig",
]
