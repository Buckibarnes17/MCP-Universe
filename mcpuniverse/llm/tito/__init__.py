"""
TITO (Token In Token Out) subpackage for RL training.

Components:
- AsyncVLLMEngine: Direct vLLM inference engine (token[] -> token[])
- TokenTrajectoryManager: Maintains token sequence, builds loss mask
- TITOLLMWrapper: Agent-compatible wrapper combining engine + manager
"""

from .engine import AsyncVLLMEngine, AsyncVLLMBackend, VLLMEngineConfig, create_ray_vllm_actor
from .manager import TokenTrajectoryManager, TokenTrajectory, TokenSegment
from .wrapper import TITOLLMWrapper, TITOLLMConfig

__all__ = [
    "AsyncVLLMEngine",
    "AsyncVLLMBackend",
    "VLLMEngineConfig",
    "create_ray_vllm_actor",
    "TokenTrajectoryManager",
    "TokenTrajectory",
    "TokenSegment",
    "TITOLLMWrapper",
    "TITOLLMConfig",
]
